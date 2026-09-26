#!/bin/bash
# hook-class: informational
# SessionStart hook (matcher startup only): measures the total byte size of
# every auto-memory store on the machine (topic files included, which sessions
# load only on demand) and nudges /memory-store-audit once that total reaches
# a count-scaled threshold: MEMORY_AUDIT_NUDGE_PER_PROJECT_BYTES (default in
# docs/memory-audit-nudge.md) per store.
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
# Order: source _lib.sh -> .source filter -> _lib_config_dir -> kill-switch ->
# override-parsing -> glob-and-measure -> threshold -> re-arm band -> fire.
# Exits 0 on every path.
#
# Kill-switch: delegates to _config_enabled's memory_audit_nudge schema row,
# checked before any filesystem scan; grammar in docs/memory-audit-nudge.md
# "How to disable".
# The legacy file <config-dir>/.memory-audit-nudge-disabled still works via
# the schema's legacy-import path.
#
# Latency bound: the settings.json registration's "timeout" is the only bound
# on the whole process; whether the harness cancels a stalled SessionStart hook
# at that value is unverified.
# With timeout(1) or gtimeout(1) on PATH, _lib_capped caps each find, awk, and
# jq process it wraps, and without either those calls run uncapped.
# GNU timeout puts its child in a new process group and signals the whole
# group, so it takes down the wc that find -exec spawns along with find
# itself in the common case. The wc can still outlive the cap in two
# narrower cases: the fully-uncapped fallback (neither timeout nor
# gtimeout on PATH), and a BusyBox timeout build, which signals only the
# direct child.
# The hook makes up to four sequential capped calls (jq parse, find, awk, and
# fire-time jq), so their cumulative worst case can exceed one cap and the
# registration timeout.
# The projects glob, mkdir -p, state and log redirects, config reads, and the
# stdin read are not under any cap.
#
# A scan whose find or awk was killed by its cap is discarded: exit 0 with the
# state file, log, and output untouched.
#
# Out of scope, each an accepted limitation rather than an oversight:
#   - No mid-session firing: the nudge arrives at the next session start.
#   - No --check query mode: nothing consumes this hook's number.
#   - No log rotation: the log is append-only, one line per fire.
#   - The state-file read-then-write is unlocked, so concurrent session starts
#     can double-fire; the hook is informational-class and loses no data.
#   - Paths containing a newline are skipped, so their bytes are not counted.
#
# Fail-open everywhere: a missing jq, an unresolvable config dir, an unreadable
# projects tree, or malformed stdin exits 0 with no stdout.

# strict mode omitted deliberately, matching nudge-handoff-near-context-cap.sh's
# own rationale: this hook always exits 0, and -e would trip on this file's
# `|| true` guards (mkdir -p, log/state-file writes) instead of letting them
# fail open.
set -uo pipefail

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

INPUT=$(cat 2>/dev/null)
SOURCE=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.source | type) == "string" then .source else empty end' 2>/dev/null)
[ "$SOURCE" = "startup" ] || exit 0

CONFIG_DIR=$(_lib_config_dir) || exit 0

# A schema-read failure (exit 3, or 4 for an absent or malformed row) leaves
# the nudge enabled: that is memory_audit_nudge's documented fail-open direction.
# _config.sh treats exits 3 and 4 identically, so keep them in one arm.
_config_enabled memory_audit_nudge
case "$?" in
  1) exit 0 ;;
  3) ;; # schema unreadable: intentional no-op, see comment above
esac

# Malformed override values (empty, literal zero, non-digit, zero-padded,
# 9+ digits) fall back to the shipped default, reusing HANDOFF_NUDGE_ABS_CAP's
# guard shape in nudge-handoff-near-context-cap.sh.
# A value degraded toward 0 would fire on every session.
# The 9-digit cutoff is a sanity bound on a plausible per-store byte size, not
# an arithmetic-wrap guard.
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

NEWLINE=$'\n'
WC_OUTPUT=""
if [ "$#" -gt 0 ]; then
  # -H follows a symlinked `memory` start point but no symlink met during traversal.
  # A path containing a newline is pruned, since it would forge extra `wc` rows below.
  # 5s is _lib_capped's shared default (guard-settings-session-keys.sh precedent).
  WC_OUTPUT=$(_lib_capped find -H "$@" -path "*$NEWLINE*" -prune -o -type f -exec wc -c {} + 2>/dev/null)
  FIND_STATUS=$?
  # A cap-killed find leaves a partial listing, so the measurement is discarded.
  if _lib_status_consistent_with_cap_kill "$FIND_STATUS"; then exit 0; fi
fi

# The wc-total-row-exclusion and project-store-count logic lives in the
# sidecar nudge-memory-store-audit.awk, invoked via -f so both this hook and
# test_nudge_memory_store_audit.py's direct `awk -f` invocations run the
# exact same program. See that file's header for the program's own
# documentation.
TOTAL_AND_COUNT=$(_lib_capped awk -f "${0%/*}/nudge-memory-store-audit.awk" <<< "$WC_OUTPUT" 2>/dev/null)
AWK_STATUS=$?
if _lib_status_consistent_with_cap_kill "$AWK_STATUS"; then exit 0; fi

# Malformed or empty awk output falls back to 0 for both values; a zero store count exits below.
TOTAL_BYTES="${TOTAL_AND_COUNT%%$'\n'*}"
PROJECT_STORE_COUNT="${TOTAL_AND_COUNT#*$'\n'}"
case "$TOTAL_BYTES" in ''|*[!0-9]*) TOTAL_BYTES=0 ;; esac
case "$PROJECT_STORE_COUNT" in ''|*[!0-9]*) PROJECT_STORE_COUNT=0 ;; esac

# No memory content anywhere on the machine means nothing to audit.
# A store count of zero must never itself be read as "already over threshold".
[ "$PROJECT_STORE_COUNT" -gt 0 ] || exit 0

THRESHOLD=$(( PER_PROJECT_BYTES * PROJECT_STORE_COUNT ))

if [ "$TOTAL_BYTES" -lt "$THRESHOLD" ] 2>/dev/null; then
  exit 0
fi

STATE_FILE="$CONFIG_DIR/.memory-audit-nudge-fired"
# Unlocked read-then-write; concurrent starts can double-fire (see header).
RECORDED_TOTAL=""
if [ -f "$STATE_FILE" ]; then
  IFS= read -r RECORDED_TOTAL < "$STATE_FILE" 2>/dev/null || RECORDED_TOTAL=""
fi
# Same malformed-value guard shape as the override guards above.
# A literal "0" is never a value this hook wrote, so it reads as no prior record and fails toward firing.
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

# Fire: build the JSON before writing state/log so a jq failure cannot consume the fire.
# shellcheck disable=SC2016 # single-quoted on purpose: every $-prefixed name below is a jq filter reference, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
OUTPUT=$(_lib_jq -n \
  --argjson total "$TOTAL_BYTES" \
  --argjson projects "$PROJECT_STORE_COUNT" \
  --argjson threshold "$THRESHOLD" \
  --argjson per_store "$PER_PROJECT_BYTES" \
  '{
    hookSpecificOutput: {
      hookEventName: "SessionStart",
      additionalContext: ("This machine'\''s Claude Code auto-memory stores now hold " + ($total|tostring) + " bytes across " + ($projects|tostring) + " project store(s), past the " + ($threshold|tostring) + "-byte size threshold (" + ($per_store|tostring) + " bytes per store; topic files load only on demand). Consider running /memory-store-audit to migrate durable, standing facts into version-controlled docs and prune what'\''s already covered elsewhere.")
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
