#!/bin/bash
# hook-class: gate
# PreToolUse: deny a no-op Agent/Task dispatch -- a subagent spawned solely
# to wait, occupy the turn, or report back immediately while other
# dispatches are in flight -- at the tool-call boundary. See
# docs/design-decisions/no-op-dispatch-hook-gate.md for the design this
# hook implements and docs/design-decisions/no-op-dispatch-guard.md for
# the two prior advisory-only attempts it supersedes.
#
# Failure direction:
#   - Deny on payload failure (malformed JSON, empty stdin, non-object
#     tool_input, missing _lib.sh) -- TestGateHookBehavior requires this
#     uniformly of every gate.
#   - No state-failure branch: this hook reads no mutable state from disk
#     -- no config dir, no git, no transcript, no marker -- so there is no
#     unresolvable state to fail open on. An empty or missing prompt
#     allows: the anchored stub arm requires at least one token, so an
#     empty string matches nothing.
#
# Known gaps, stated rather than left implicit -- required by
# `plugins/claude-hook-review/skills/claude-hook-review/SKILL.md` §
# "9. Review checklist". See docs/design-decisions/no-op-dispatch-hook-gate.md's
# Known gaps section for the corpus-shape, description-only-tell, and
# false-positive-residual gaps.
#   - If `grep` is missing from PATH, the `grep -qiE` calls below return
#     127 (false) and execution falls through to allow. This is the one
#     silent fail-open path in an otherwise fail-closed script.
#   - If `tr` is missing from PATH instead, the earlier `tr -s` command
#     substitution that builds COLLAPSED_PROMPT fails and yields an empty
#     string. An empty COLLAPSED_PROMPT no-matches both grep arms below,
#     reaching the same fail-open outcome by a different mechanism.
#   - Neither `tr` nor `grep` has a timeout wrap; a hung (not missing) one
#     blocks every dispatch until the harness's own timeout intervenes --
#     see the decision doc's Known gaps for why this is accepted rather
#     than wrapped.

set -uo pipefail

DENY_GATE_LABEL="no-op-dispatch"

# A prompt at or above this length is allowed regardless of content -- see
# docs/design-decisions/no-op-dispatch-hook-gate.md for the corpus figures
# this ceiling is grounded in.
# ${#PROMPT} counts bytes in the C locale and characters in a UTF-8 one.
# A multibyte prompt therefore measures at or above its true character
# count, which can only move a dispatch out of this gate's reach, never
# into it.
NOOP_MAX_PROMPT_LEN=600

# Every idiom below traces to one of two sources: it appears verbatim in a
# confirmed no-op dispatch from this repo's own transcript history, or it
# is named verbatim in claude/.claude/CLAUDE.md's Agent Briefing bullet as
# a prohibited shape. An idiom grounded in neither does not go in either
# list -- see docs/design-decisions/no-op-dispatch-hook-gate.md.
#
# Anchored against the whole (whitespace-collapsed) prompt: the entire
# prompt is one no-op token, optionally with trailing punctuation. Two
# confirmed corpus prompts are exactly `noop` and `placeholder`. The
# remaining tokens name the same "do nothing" shape.
NOOP_STUB_TOKEN_RE='^(noop|no-op|placeholder|wait|nothing|standby|stand by|ack)[[:punct:]]*$'

# Unanchored against the prompt alone, per the same two-source grounding
# rule as NOOP_STUB_TOKEN_RE above. A read-scoping instruction ("do not
# read any files") is deliberately excluded -- see the decision doc for
# why.
NOOP_PHRASE_RE='do(ing|es)? nothing|just wait|report back immediately|exists only (so|to)|no action|occupy the turn|hold while'

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source -- see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment).
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

# Self-filter on tool name, registered on the union Agent|Task -- the same
# matcher shape as require-architect-consult.sh's own registration. The
# harness's confirmed dispatch tool name is "Agent". Task covers a future
# Task-dispatched spawn.
# Every dispatch is evaluated regardless of subagent_type -- see the
# design-decision file for why this gate carries no subagent_type filter.
case "$TOOL_NAME" in
  Agent | Task) ;;
  *) exit 0 ;;
esac

PROMPT=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.prompt // empty' 2>/dev/null)

# The conjunction is the whole design: a prompt at or above the ceiling is
# allowed no matter what it contains, so this gate is structurally unable
# to reach a prompt long enough to specify real work.
[ "${#PROMPT}" -lt "$NOOP_MAX_PROMPT_LEN" ] || exit 0

# Whitespace-collapse before either arm, so a no-op instruction wrapped
# across a newline ("do\nnothing") still matches a single-line grep.
COLLAPSED_PROMPT=$(printf '%s' "$PROMPT" | tr -s '[:space:]' ' ')

# `tr -s` squeezes whitespace runs but leaves a single leading or
# trailing space uncollapsed, which the anchored stub regex below cannot
# match. Strip at most one from each end to compensate.
COLLAPSED_PROMPT="${COLLAPSED_PROMPT# }"
COLLAPSED_PROMPT="${COLLAPSED_PROMPT% }"

# `tr -s '[:space:]'` and the trim above operate on ASCII whitespace only.
# A stub token or phrase padded or separated with non-ASCII whitespace
# (e.g. U+00A0 NBSP) passes through both untouched, an accepted scope
# limit rather than a bug to fix -- see
# docs/design-decisions/no-op-dispatch-hook-gate.md's Known gaps section
# for the platform-dependence of this claim.

if printf '%s' "$COLLAPSED_PROMPT" | grep -qiE "$NOOP_STUB_TOKEN_RE" || printf '%s' "$COLLAPSED_PROMPT" | grep -qiE "$NOOP_PHRASE_RE"; then
  emit_deny "this dispatch's prompt is short and instructs the agent to do no work. CLAUDE.md §Agent Briefing bars dispatching an agent — of any type — whose instructions are to report back immediately, occupy the turn, or hold while other dispatches finish. A no-op agent returns at once, so it waits for nothing, and still pays a full agent's context cost for an empty return. When pending dispatches are all that remain, end the turn without a tool call and let their completion drive the next one. If this dispatch does have real work to do, state that work in the prompt and retry — a prompt long enough to specify a task does not trip this gate. If you are a subagent, report this denial to your dispatcher rather than attempting to resolve it yourself."
  exit 0
fi

exit 0
