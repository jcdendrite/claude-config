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
# ai-instruction-and-memory-files/SKILL.md is capped at 215: the
# require-memory-skill.sh gate loads it at the exact moment a memory write
# is happening, so its routing content can't move to a narrower surface.
#
# The "if" field in settings.json is unreliable — the internal
# _lib_command_invokes_git_subcmd check is the actual gate. See
# require-code-review.sh for the same pattern and rationale.
#
# On a machine lacking both timeout(1) and gtimeout(1), _lib_capped runs the
# git calls below uncapped, so a stalled git (locked index, network mount)
# hangs this gate rather than degrading gracefully.

set -uo pipefail

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by skill length gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by skill length gate: could not parse tool-input JSON."

# Only gate Bash tool calls.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Only gate git commit commands. Checked and fail-closed, matching this
# gate's documented fail-closed posture: an undetermined match (sed/tr
# missing, killed, or erroring inside the helper) must not silently skip
# the length check.
_lib_command_invokes_git_subcmd "$COMMAND" commit
GIT_COMMIT_MATCH_STATUS=$?
if [ "$GIT_COMMIT_MATCH_STATUS" -eq 1 ]; then
  exit 0
fi
if [ "$GIT_COMMIT_MATCH_STATUS" -ne 0 ]; then
  emit_deny "Blocked by skill length gate: could not determine whether this command invokes git commit (status ${GIT_COMMIT_MATCH_STATUS}) — sed/tr may be missing, killed, or errored. Failing closed rather than letting an unscanned git commit bypass the length check."
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
[ -z "$REPO_ROOT" ] && exit 0

# Per-skill limit override. Listed paths are repo-root-relative.
limit_for() {
  case "$1" in
    claude/.claude/skills/code-review/SKILL.md|claude/.claude/skills/plan-review/SKILL.md|claude/.claude/skills/plan-review/ROUTING.md)
      echo 500 ;;
    claude/.claude/skills/pr-description/SKILL.md)
      echo 210 ;;
    claude/.claude/skills/ai-instruction-and-memory-files/SKILL.md)
      echo 215 ;;
    *)
      echo 200 ;;
  esac
}

FAIL=0
MESSAGES=""
while IFS= read -r f; do
  new=$(_lib_capped git show ":$f" 2>/dev/null | awk 'END{print NR}')
  old=$(_lib_capped git show "HEAD:$f" 2>/dev/null | awk 'END{print NR}')
  limit=$(limit_for "$f")
  if [ "$new" -gt "$limit" ] && [ "$new" -gt "$old" ]; then
    MESSAGES="${MESSAGES}  $f: $new lines (was $old, limit $limit)\n"
    FAIL=1
  fi
# Path prefixes are repo-root-relative for this repo's layout.
# Covers both stowed skills (claude/.claude/skills/) and project-scoped plugins (plugins/*/skills/),
# plus the single hardcoded plan-review/ROUTING.md exception (see limit_for() above).
# In other repos this grep matches nothing and the hook exits 0 silently.
done < <(git diff --cached --name-only | grep -E '(claude/.claude/skills/|plugins/[^/]+/skills/).+/SKILL\.md|^claude/\.claude/skills/plan-review/ROUTING\.md$')

if [ "$FAIL" -eq 1 ]; then
  REASON=$(printf 'Skill length gate: one or more SKILL.md files grew past their per-skill limit. Reduce to the limit or fewer lines before committing:\n%b' "$MESSAGES")
  emit_deny "$REASON"
fi
