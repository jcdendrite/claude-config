#!/bin/bash
# hook-class: gate
# Gate: block git commit when a staged SKILL.md grows past its per-skill ceiling.
#
# Policy: deny when the staged file is over its limit AND longer than the
# previously committed version. This allows reducing an already-over-limit
# file commit by commit without blocking the work, while still catching new
# bloat.
#
# Default limit is 200 lines. Structural-dispatcher skills (code-review,
# plan-review) carry item-ownership / routing tables that legitimately run
# longer and are capped at Anthropic's documented 500-line ceiling instead.
# plan-review/ROUTING.md gets the same 500-line cap: it holds the
# item-ownership / spawn-routing table extracted from plan-review/SKILL.md,
# the same content class the cap was written for.
# Plugin-scoped skills (plugins/*/skills/) currently have no override path
# and all fall to the 200-line default — extend limit_for() if a plugin
# skill earns the same exception.
# pr-description/SKILL.md is capped at 210: it writes one paragraph per
# line with no hard-wrap, so trimming words never reduces the line count,
# only removing a whole paragraph, heading, or blank line does.
#
# The "if" field in settings.json is unreliable — the internal
# _lib_command_invokes_git_subcmd check is the actual gate. See
# require-code-review.sh for the same pattern and rationale.
#
# On a machine lacking both timeout(1) and gtimeout(1), _lib_capped runs the
# git calls below uncapped, so a stalled git (locked index, network mount)
# hangs this gate rather than degrading gracefully.
#
# The commit-detection, repo-root, growth-comparison, and deny-message logic
# is shared with check-claude-md-length.sh via _lib_staged_length_gate in
# _lib.sh — this file supplies only the staged-path pattern and limit_for.

set -uo pipefail

DENY_GATE_LABEL="skill length"

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

# Only gate Bash tool calls.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Per-skill limit override. Listed paths are repo-root-relative.
limit_for() {
  case "$1" in
    claude-skills/skills/code-review/SKILL.md|claude-skills/skills/plan-review/SKILL.md|claude-skills/skills/plan-review/ROUTING.md)
      echo 500 ;;
    claude-skills/skills/pr-description/SKILL.md)
      echo 210 ;;
    *)
      echo 200 ;;
  esac
}

# Path prefixes are repo-root-relative for this repo's layout. Covers both
# stowed skills (claude-skills/skills/) and project-scoped plugins
# (plugins/*/skills/), plus the single hardcoded plan-review/ROUTING.md
# exception (see limit_for() above). In other repos this pattern matches
# nothing and the gate exits 0 silently.
_lib_staged_length_gate '(claude-skills/skills/|plugins/[^/]+/skills/).+/SKILL\.md|^claude-skills/skills/plan-review/ROUTING\.md$' "one or more SKILL.md files grew past their per-skill limit."
