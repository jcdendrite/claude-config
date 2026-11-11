#!/usr/bin/env bash
# hook-class: informational
# Warns on screen and to the agent when the harness PATH's python3 is missing, reports no version, does not answer within the probe cap, or is below _config.sh's Python floor.
# SessionStart cannot refuse a session, so this hook only warns.
# Every path exits 0, and the hook prints nothing unless the probe returns 1 or 2.
# The settings.json registration's startup|resume matcher and the in-script .source filter must stay in sync.
# Only the floor constants and a digit-validated version reach the output, so no path or free text reaches the screen or the model.
# _lib_jq builds the notice, so a quote in its text cannot break the JSON line.
# The agent text limits the agent to reporting, because the fix is the engineer's call.
# A bash below _config.sh's bash floor fails the _lib.sh source, so the hook then exits 0 with no notice.
# A missing or hung jq also ends the hook with no notice.
# The 2-second probe cap needs timeout or gtimeout on PATH, and without one the probe runs uncapped.
# docs/hooks.md § "Python version" records that delivery in -p mode and to subagents is unverified.
# The hook may exceed the 100 ms per-fire budget in plugins/claude-hook-review/skills/claude-hook-review/SKILL.md § "7. Performance budget", because it starts jq and a python3 interpreter and fires only at session start and resume.
set -uo pipefail
exec 2>/dev/null

INPUT=$(cat) || exit 0

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

# Repo CLAUDE.md "Hook defense-in-depth": the hook filters .source itself.
payload_source=$(printf '%s\n' "$INPUT" \
  | _lib_jq -r 'select(.hook_event_name == "SessionStart" and (.source == "startup" or .source == "resume")) | .source') || exit 0
[ -n "$payload_source" ] || exit 0

python3_probe_cap_seconds=2
python3_floor_status=0
python3_found_version=$(_python3_floor_status _lib_capped_for "$python3_probe_cap_seconds") || python3_floor_status=$?
[ "$python3_floor_status" -ne 0 ] || exit 0

python_floor="$_PYTHON_FLOOR_MAJOR.$_PYTHON_FLOOR_MINOR"
if [ "$python3_floor_status" -eq 1 ]; then
  python3_state="is $python3_found_version"
  python3_remedy="Install Python $python_floor or newer, put its python3 first on that PATH, and restart Claude Code."
else
  python3_state="is missing, reported no version, or did not answer within $python3_probe_cap_seconds seconds"
  python3_remedy="Run \`env python3 --version\` in this directory before installing anything: if it prints Python $python_floor or newer, python3 was only slow to answer and nothing needs installing. Otherwise install Python $python_floor or newer, put its python3 first on that PATH, and restart Claude Code."
fi

python3_condition="python3 on the PATH Claude Code started with, in the session's working directory, $python3_state, and claude-config's hooks support Python $python_floor or newer."
screen_text="$python3_condition $python3_remedy See README.md § \"Requirements\" in the claude-config repository."
agent_text="$python3_condition Hooks that run python3 are unsupported until then, and docs/hooks.md § \"Python version\" lists what each does if its helper fails. The agent's only duty is to tell the engineer in its next reply and point them to README.md § \"Requirements\", and it does nothing else about this unless the engineer asks."

# shellcheck disable=SC2016 # single-quoted on purpose: $screen and $agent are jq --arg bindings, not shell variables.
_lib_jq -c -n --arg screen "$screen_text" --arg agent "$agent_text" \
  '{systemMessage: $screen, hookSpecificOutput: {hookEventName: "SessionStart", additionalContext: $agent}}' || true
exit 0
