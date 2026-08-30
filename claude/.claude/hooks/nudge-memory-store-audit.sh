#!/bin/bash
# hook-class: informational
# SessionStart hook (matcher startup only): measures the total byte size of
# every auto-memory store on the machine and nudges /memory-store-audit once
# that total outgrows one session's documented MEMORY.md load budget per store.
# Never blocks, never edits, and never names a project or a path.
# See docs/memory-audit-nudge.md for the threshold derivation, the count-scaled
# formula, the re-arm band, the state-file and log formats, and the scan's
# start-point rule — none of it restated here.
#
# Self-filters on .source == "startup" per the repo's hook defense-in-depth rule.
# On-disk store size is not session-scoped, so it must not re-fire on
# clear/compact/resume — the same argument check-branch-divergence.sh makes for
# its own startup-only matcher.
#
# Order: source _lib.sh (before reading stdin, so _lib_jq's timeout backstop
# covers the .source filter too) -> .source filter -> _lib_config_dir ->
# kill-switch -> timeout-binary precondition -> glob-and-measure -> threshold ->
# re-arm band -> fire.
# Exits 0 on every path.
#
# Kill-switch: touching <config-dir>/.memory-audit-nudge-disabled suppresses
# every future fire, checked before any filesystem scan.
#
# The scan is skipped entirely when neither timeout(1) nor gtimeout(1) resolves.
# _lib_capped_for would otherwise run the filesystem walk uncapped, and a
# stalled path would hold session start itself open with no bound.
#
# Out of scope, each an accepted limitation rather than an oversight:
#   - No mid-session firing: the nudge arrives at the next session start.
#   - No --check query mode: nothing consumes this hook's number.
#   - No log rotation: the log is append-only, one line per fire.
#
# Fail-open everywhere: a missing jq, an unresolvable config dir, an unreadable
# projects tree, or malformed stdin exits 0 with no stdout.

set -uo pipefail

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

INPUT=$(cat 2>/dev/null)
SOURCE=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.source | type) == "string" then .source else empty end' 2>/dev/null)
[ "$SOURCE" = "startup" ] || exit 0

CONFIG_DIR=$(_lib_config_dir) || exit 0

[ -f "$CONFIG_DIR/.memory-audit-nudge-disabled" ] && exit 0

_lib_timeout_binary_available || exit 0

# Malformed override values (empty, literal zero, non-digit, zero-padded,
# 10+ digits) fall back to the shipped default, reusing HANDOFF_NUDGE_ABS_CAP's
# guard shape in nudge-handoff-near-context-cap.sh.
# A value degraded toward 0 would fire on every session.
# A 10+ digit value risks wrapping negative in bash's signed 64-bit arithmetic.
case "${MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES:-}" in
  ''|0|*[!0-9]*|0[0-9]*|?????????*) PER_PROJECT_BYTES=25600 ;;
  *) PER_PROJECT_BYTES=$MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES ;;
esac
case "${MEMORY_AUDIT_NUDGE_REARM_BYTES:-}" in
  ''|0|*[!0-9]*|0[0-9]*|?????????*) REARM_BYTES=25600 ;;
  *) REARM_BYTES=$MEMORY_AUDIT_NUDGE_REARM_BYTES ;;
esac

# nullglob makes a zero-match pattern expand to nothing rather than the literal
# pattern string.
# noglob is saved and restored so a caller running under `set -f` is unaffected.
# Positional parameters rather than a named array: under `set -u` a named array
# assigned from a zero-match glob is unbound on some bash builds, while a
# zero-element "$@" is exempt from nounset by definition.
NULLGLOB_WAS_SET=0
NOGLOB_WAS_SET=0
if shopt -q nullglob; then NULLGLOB_WAS_SET=1; fi
case $- in *f*) NOGLOB_WAS_SET=1 ;; esac
shopt -s nullglob
set +f
set -- "$CONFIG_DIR"/projects/*/memory
if [ "$NULLGLOB_WAS_SET" -eq 0 ]; then shopt -u nullglob; fi
if [ "$NOGLOB_WAS_SET" -eq 1 ]; then set -f; fi

WC_OUTPUT=""
if [ "$#" -gt 0 ]; then
  WC_OUTPUT=$(_lib_capped_for 5 find "$@" -type f -exec wc -c {} + 2>/dev/null)
fi

# HOOK_TEST_FIXTURE: wc-total-row-awk — start
# test_nudge_memory_store_audit.py extracts this program verbatim between
# these sentinels.
# Keep both lines.
# `find -exec ... {} +` may batch into several wc invocations, and each batch
# holding more than one file emits its own "total" row.
# Summing $1 unconditionally would count those rows as if they were files.
# Every per-file line's path is absolute and therefore contains "/", while the
# "total" row's remaining text is the bare word "total" and has none.
# Stripping the leading count field and testing for "/" discriminates the two
# without depending on line position or batch count.
# A second pass tallying the project-store count with one `grep` per project
# directory against the whole wc output would cost O(project count x file
# count). Every per-file path already carries its own project's memory
# directory as a prefix (.../projects/<project>/memory/...), so this single
# pass buckets by that prefix (the text up to the rightmost "/memory/")
# instead, tallying both totals in one O(file count) scan.
# Prints two lines: the byte total, then the project-store count.
TOTAL_AND_COUNT=$(_lib_capped_for 5 awk '
  {
    count = $1
    path = $0
    sub(/^[ \t]*[0-9]+[ \t]+/, "", path)
    if (index(path, "/") == 0) next
    sum += count
    if (match(path, /^.*\/memory\//)) seen[substr(path, 1, RLENGTH - 1)] = 1
  }
  END {
    print sum + 0
    print length(seen)
  }
' <<< "$WC_OUTPUT" 2>/dev/null)
# HOOK_TEST_FIXTURE: wc-total-row-awk — end

# Malformed or empty awk output (a killed-by-timeout pass, though the
# precondition above guarantees timeout(1)/gtimeout(1) is on PATH) falls back
# to 0 for both.
# The store-count-must-be-positive check below already treats a
# PROJECT_STORE_COUNT of 0 as nothing to audit.
TOTAL_BYTES="${TOTAL_AND_COUNT%%$'\n'*}"
PROJECT_STORE_COUNT="${TOTAL_AND_COUNT#*$'\n'}"
case "$TOTAL_BYTES" in ''|*[!0-9]*) TOTAL_BYTES=0 ;; esac
case "$PROJECT_STORE_COUNT" in ''|*[!0-9]*) PROJECT_STORE_COUNT=0 ;; esac

# No memory content anywhere on the machine: nothing to audit, and the
# count-scaled threshold below is undefined at N=0 (not a division, but a
# store-count of zero must never itself be read as "already over budget").
[ "$PROJECT_STORE_COUNT" -gt 0 ] || exit 0

THRESHOLD=$(( PER_PROJECT_BYTES * PROJECT_STORE_COUNT ))

if [ "$TOTAL_BYTES" -lt "$THRESHOLD" ] 2>/dev/null; then
  exit 0
fi

STATE_FILE="$CONFIG_DIR/.memory-audit-nudge-fired"
# This read-then-write is not lock-protected: two near-simultaneous session
# starts on the same machine can double-fire or stomp each other's
# high-water mark. Low severity for this informational-class hook (no data
# loss), so deferred rather than fixed.
RECORDED_TOTAL=""
if [ -f "$STATE_FILE" ]; then
  IFS= read -r RECORDED_TOTAL < "$STATE_FILE" 2>/dev/null || RECORDED_TOTAL=""
fi
# Same malformed-value guard shape as the override guards above. A literal
# "0" is never a value this hook itself would have written (a real fire only
# ever happens at TOTAL_BYTES >= THRESHOLD > 0), so it is treated as no
# prior record rather than a real recorded total -- fail toward firing, not
# toward silent suppression.
case "$RECORDED_TOTAL" in ''|0|*[!0-9]*|0[0-9]*|?????????*) RECORDED_TOTAL="" ;; esac

mkdir -p "$CONFIG_DIR" 2>/dev/null || true

if [ -n "$RECORDED_TOTAL" ] && [ "$TOTAL_BYTES" -lt "$RECORDED_TOTAL" ] 2>/dev/null; then
  # Shrink: a partial audit brought the total back down but not below
  # threshold. Rewrite the high-water mark without firing, so the next
  # genuine crossing re-arms from here rather than from the old peak.
  printf '%s\n' "$TOTAL_BYTES" > "$STATE_FILE" 2>/dev/null || true
  exit 0
fi

if [ -n "$RECORDED_TOTAL" ] && [ "$TOTAL_BYTES" -lt "$(( RECORDED_TOTAL + REARM_BYTES ))" ] 2>/dev/null; then
  # Already fired for this band; not yet re-armed.
  exit 0
fi

# Fire: build the nudge JSON first and only write the state file/log if it
# actually produced output, mirroring nudge-handoff-near-context-cap.sh's own
# build-before-write ordering so a jq failure can't burn the session's one
# shot with nothing to show for it.
# shellcheck disable=SC2016 # single-quoted on purpose: every $-prefixed name below is a jq filter reference, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
OUTPUT=$(_lib_jq -n \
  --argjson total "$TOTAL_BYTES" \
  --argjson projects "$PROJECT_STORE_COUNT" \
  --argjson threshold "$THRESHOLD" \
  '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: ("This machine'\''s Claude Code auto-memory stores now hold " + ($total|tostring) + " bytes across " + ($projects|tostring) + " project store(s), past the " + ($threshold|tostring) + "-byte per-session load-budget threshold. Consider running /memory-store-audit to migrate durable, standing facts into version-controlled docs and prune what'\''s already covered elsewhere.")
    }
  }' 2>/dev/null)

if [ -n "$OUTPUT" ]; then
  NUDGE_LOG="$CONFIG_DIR/.memory-audit-nudge.log"
  printf 'nudged total=%s projects=%s threshold=%s source=startup\n' \
    "$TOTAL_BYTES" "$PROJECT_STORE_COUNT" "$THRESHOLD" >> "$NUDGE_LOG" 2>/dev/null || true
  printf '%s\n' "$TOTAL_BYTES" > "$STATE_FILE" 2>/dev/null || true
  printf '%s' "$OUTPUT"
fi

exit 0
