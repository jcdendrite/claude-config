#!/bin/bash
# hook-class: gate
set -uo pipefail

DENY_GATE_LABEL="routing-read"

# PreToolUse: during an active plan-review session, require that ROUTING.md
# was Read (tracked by log-routing-read.sh) before any Agent spawn.
#
# tool_name for sub-agent spawning is "Agent" (verified from session transcripts).
# This hook deliberately does NOT cover Bash matchers — sub-agent spawning is
# via the dedicated Agent tool, not shell.

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

[ "$TOOL_NAME" = "Agent" ] || exit 0

# Absent session_id (older Claude Code versions, payload-schema drift) has no
# marker to check at all, so there is nothing to distinguish "plan-review
# active" from "not active" and the safer default is to allow.
[ -n "$SESSION_ID" ] || exit 0

# An id that is not a safe single path component (e.g. containing "../") is
# never concatenated into a marker path, so it leaves this hook in the same
# position as an absent one: ACTIVE_MARKER is what distinguishes "plan-review
# active" from "not active", and without a usable id that question cannot be
# answered. Allow, for the same reason the absent case allows.
#
# Note this is the opposite default from the bypass-shaped gates
# (require-memory-skill.sh, require-respond-pr.sh), where the marker grants an
# exception to a standing deny and an unusable id must therefore withhold it.
# Here the marker turns enforcement ON, so an unusable id must not turn it on.
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# An unresolvable config dir leaves "is plan-review active" undecidable, so
# this exits open (0) the same as an unusable SESSION_ID above -- the marker
# is what turns enforcement ON, not what turns it off.
CONFIG_DIR=$(_lib_config_dir) || exit 0

# Only enforce during an active plan-review session.
ACTIVE_MARKER="$CONFIG_DIR/.plan-review-active.d/$SESSION_ID"
[ -f "$ACTIVE_MARKER" ] || exit 0

# Allow if a fresh (< 60 min) routing-read marker exists.
ROUTING_MARKER="$CONFIG_DIR/.plan-review-routing-read.d/$SESSION_ID"
if [ -f "$ROUTING_MARKER" ] && [ -n "$(find "$ROUTING_MARKER" -mmin -60 2>/dev/null)" ]; then
  exit 0
fi

emit_deny "Agent spawn — use the Read tool to read ~/.claude/skills/plan-review/ROUTING.md before spawning any specialist agent — a Bash read (cat/sed/grep) does not satisfy this gate. All spawn criteria (always-spawn rules, item ownership, reconciliation logic) live exclusively in ROUTING.md."
