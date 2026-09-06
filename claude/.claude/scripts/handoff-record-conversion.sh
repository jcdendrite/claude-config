#!/bin/bash
# Records a handoff-file write against nudge-handoff-near-context-cap.sh's own
# log. No args.
# See claude/.claude/skills/handoff/SKILL.md's "record the conversion signal"
# section for the caller and the canonical recipe this implements.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

# Best-effort telemetry, not a gate: silently skip the log append when
# this session's id can't be resolved.
resolved=$(_lib_resolve_claude_pid) || exit 0
session_id="${resolved%% *}"

# Reject a session id carrying whitespace, which would corrupt the log
# line's key=value tokenization.
_lib_valid_session_id_component "$session_id" || exit 0

config_dir=$(_lib_config_dir) || exit 0

printf 'handoff session=%s\n' "$session_id" >> "$config_dir/.handoff-nudge.log"
