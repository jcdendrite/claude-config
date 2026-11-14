#!/usr/bin/env bash
# hook-class: informational
# SessionStart (compact only): re-injects the facts a compaction summary tends to drop, as additionalContext after the
# summary. The block comes from `compaction-context.py state`: the skills loaded this session with their SKILL.md paths, the
# branch's plan file, and the newest quoted prompts. See docs/hooks.md.
#
# Advisory only: this emits additionalContext, not a gate. It cannot steer the summary itself.
#
# The "compact" matcher in settings.json and the .source == "compact" self-filter below must stay paired.
#
# Fail-open: every failure path (missing python3, jq, script, transcript, or a script error) exits 0 with no output.
# A 6-second time cap, not a byte cap, bounds the transcript read.
# The cap plus 2 seconds of kill grace leaves 2 seconds of the 10-second settings timeout for the bootstrap and the jq calls.
# Without timeout or gtimeout on PATH the script call runs uncapped, bounded only by the settings timeout.
# A fire costs a few hundred milliseconds because it starts python3, which is outside the usual per-fire hook budget and
# acceptable here because the hook fires only after a compaction.
#
# A secret pasted into a quoted message is not redacted.
# The quoted text is already in the session's own transcript.
# The block reaches jq on stdin and never as an argument, because another local user can read a process's argv on some hosts.
#
# Exit 0 always -- a SessionStart hook has no deny path, so erroring here would only block session startup.

set -uo pipefail

# _lib.sh is sourced before stdin is read so every jq call, the .source filter included, routes through _lib_jq's
# timeout backstop.
if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

INPUT=$(cat 2>/dev/null)
SOURCE=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.source | type) == "string" then .source else empty end' 2>/dev/null)
[ "$SOURCE" = "compact" ] || exit 0

command -v python3 >/dev/null 2>&1 || exit 0

# Located from the hook's own directory, like bootstrap-project-plugins.sh's reconciler, so the hook and the script
# come from the same checkout.
STATE_SCRIPT="${0%/*}/../scripts/compaction-context.py"
[ -f "$STATE_SCRIPT" ] || exit 0

TRANSCRIPT_PATH=$(printf '%s' "$INPUT" | _lib_jq -r 'if (.transcript_path | type) == "string" then .transcript_path else empty end' 2>/dev/null)
[ -n "$TRANSCRIPT_PATH" ] && [ -f "$TRANSCRIPT_PATH" ] && [ -r "$TRANSCRIPT_PATH" ] || exit 0

STATE_BLOCK=$(_lib_capped_for 6 python3 "$STATE_SCRIPT" state --transcript "$TRANSCRIPT_PATH" 2>/dev/null) || exit 0
[ -n "$STATE_BLOCK" ] || exit 0

# shellcheck disable=SC2016 # single-quoted on purpose: `.` is jq's input, not a shell expansion.
printf '%s' "$STATE_BLOCK" | _lib_jq -Rs \
  '{hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: .}}' 2>/dev/null || true
exit 0
