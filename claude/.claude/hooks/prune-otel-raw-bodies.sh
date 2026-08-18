#!/bin/bash
# hook-class: informational
# SessionStart hook (matcher startup): bounds ~/.claude/otel-raw-bodies/ --
# the raw wire-format API request/response capture directory written when
# OTEL_LOG_RAW_API_BODIES=file:<dir> is armed -- by age (7 days) and by
# total size (5 GiB ceiling), oldest-first. See
# docs/otel-raw-body-pruning.md for the retention policy and why the path
# is a pinned constant rather than read from config.
#
# Exit 0 always, given a timeout backstop (timeout/gtimeout) on PATH --
# without one, every find/stat/rm call below runs uncapped.
#
# Known gaps (see docs/otel-raw-body-pruning.md's "Exposure facts" section):
# - Enforced only at session start -- a long-running session can write well
#   past both bounds before the next SessionStart prunes it back down.
# - Both passes walk a snapshot taken at hook-invocation time; a file a
#   concurrent session is still writing can be deleted mid-write.
# - Worst-case added SessionStart latency is ~15s (three sequential
#   5s-capped find/stat passes) once the directory holds many files.
# - A hung find -exec grandchild (a stalled rm or stat) is orphaned, not
#   killed, when its timeout wrapper expires -- inherent to timeout + find
#   -exec generally, not specific to this hook.
# - The symlink-refusal check below and the cd that trusts it aren't
#   atomic; a same-user attacker who could win that race already has
#   direct access to anything it would redirect into, so this is accepted.
#
# -e (unlike sibling SessionStart hooks' set -uo pipefail) is intentional:
# every risky command below is covered by || true or an if/while-condition
# exemption, so -e adds fail-fast on anything that isn't.
set -euo pipefail

[ -n "${HOME:-}" ] || exit 0
CAPTURE_DIR="$HOME/.claude/otel-raw-bodies"

# Mirrors track-permission-prompts.sh's own write-target symlink refusal.
[ -L "$CAPTURE_DIR" ] && exit 0
[ -d "$CAPTURE_DIR" ] || exit 0

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# A stat size field this hook feeds into arithmetic must be a plain
# non-negative integer -- under set -u, `$((TOTAL + SIZE))` with any other
# value (a raced/truncated stat line) treats SIZE as an unset-variable
# reference and aborts the whole hook non-zero, breaking "exit 0 always".
_is_size_field() {
  case "$1" in
    '' | *[!0-9]*) return 1 ;;
    *) return 0 ;;
  esac
}

# OTEL_PRUNE_MAX_BYTES is test-only (docs/otel-raw-body-pruning.md); any
# non-positive-integer value falls back to the production literal so it
# can't silently change what this irreversible delete removes.
PRODUCTION_MAX_BYTES=5368709120 # 5 GiB
MAX_BYTES="$PRODUCTION_MAX_BYTES"
if [ -n "${OTEL_PRUNE_MAX_BYTES:-}" ] \
  && printf '%s' "$OTEL_PRUNE_MAX_BYTES" | grep -qE '^[1-9][0-9]*$'; then
  MAX_BYTES="$OTEL_PRUNE_MAX_BYTES"
fi

# cd into the capture dir first so find/stat only ever see vendor-controlled
# leaf names, never the $HOME-rooted path.
cd -- "$CAPTURE_DIR" || exit 0
NAME_FILTER=(\( -name '*.request.json' -o -name '*.response.json' \))

# Portable stat: GNU coreutils (-c) probed first, BSD/macOS (-f) as
# fallback -- same pattern as deny-data-file-reads.sh's file_size() and
# ask-new-dependency-disclosure.sh's _file_size(), extended here to a
# batched multi-file format string rather than one file at a time.
if stat -c '%s' . >/dev/null 2>&1; then
  STAT_LISTING_FMT=(-c '%Y %s %n')
  STAT_SIZE_ONLY_FMT=(-c '%s')
else
  STAT_LISTING_FMT=(-f '%m %z %N')
  STAT_SIZE_ONLY_FMT=(-f '%z')
fi

# --- Age pass ------------------------------------------------------------
# -mtime +6 is the verified 7-day expression (BSD -mtime truncates to 24h
# periods; +7 matches files 8+ days old, +6 matches 7+ days old). Never
# pass -L to find/stat below -- a symlink inside the directory could
# redirect the walk outside the pinned path. rm -f already tolerates a
# file a concurrent session deleted out from under this walk.
_lib_capped_for 5 find . -maxdepth 1 -type f "${NAME_FILTER[@]}" \
  -mtime +6 -exec rm -f -- {} + 2>/dev/null || true

# --- Size pass -------------------------------------------------------------
# A file stat can no longer see (raced by a concurrent prune) drops out of
# the listing rather than aborting the walk.
LISTING=$(_lib_capped_for 5 find . -maxdepth 1 -type f "${NAME_FILTER[@]}" \
  -exec stat "${STAT_LISTING_FMT[@]}" {} + 2>/dev/null) || true

TOTAL_BYTES=0
if [ -n "$LISTING" ]; then
  while IFS=' ' read -r _MTIME SIZE _NAME; do
    _is_size_field "$SIZE" || continue
    TOTAL_BYTES=$((TOTAL_BYTES + SIZE))
  done <<<"$LISTING"
fi

if [ "$TOTAL_BYTES" -gt "$MAX_BYTES" ]; then
  # sort -n (not relying on epoch seconds being fixed-width) orders oldest
  # mtime first; delete until the running total is back under the ceiling.
  REMAINING_BYTES="$TOTAL_BYTES"
  while IFS=' ' read -r _MTIME SIZE NAME; do
    [ -n "$NAME" ] || continue
    _is_size_field "$SIZE" || continue
    [ "$REMAINING_BYTES" -le "$MAX_BYTES" ] && break
    _lib_capped_for 5 rm -f -- "$NAME" 2>/dev/null || true
    REMAINING_BYTES=$((REMAINING_BYTES - SIZE))
  done <<<"$(printf '%s\n' "$LISTING" | sort -n)"
fi

# --- Report ----------------------------------------------------------------
# systemMessage (human-visible), not hookSpecificOutput.additionalContext --
# this number is for the engineer retuning the ceiling, not for the model
# to read every session.
POST_LISTING=$(_lib_capped_for 5 find . -maxdepth 1 -type f "${NAME_FILTER[@]}" \
  -exec stat "${STAT_SIZE_ONLY_FMT[@]}" {} + 2>/dev/null) || true
POST_BYTES=0
if [ -n "$POST_LISTING" ]; then
  while IFS= read -r SIZE; do
    _is_size_field "$SIZE" || continue
    POST_BYTES=$((POST_BYTES + SIZE))
  done <<<"$POST_LISTING"
fi

POST_MIB=$((POST_BYTES / 1048576))
CEILING_MIB=$((MAX_BYTES / 1048576))
printf '{"systemMessage": "otel-raw-bodies: %s MiB after pruning (ceiling %s MiB)"}\n' \
  "$POST_MIB" "$CEILING_MIB" || true

exit 0
