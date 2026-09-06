#!/bin/bash
# hook-class: gate
# PreToolUse hook: block git commit when claude/.claude/settings.json has
# machine-local or session-scoped keys staged relative to the repo's default
# branch — see GUARDED_KEYS_JSON below for the guarded set.
#
# Purpose: every guarded key holds one machine's own state, and several are
# written into the user settings file by Claude Code rather than by hand —
# model and effortLevel from /config, skipAutoPermissionPrompt when it
# records the permission-prompt preference. Committing any of them ships one
# engineer's local state as the shipped config for every user. This hook
# catches that class of accidental commit and surfaces it before git runs.
#
# Defense-in-depth: the hook filters its own input by tool name AND checks
# whether settings.json is actually staged — do not rely solely on the
# settings.json `if` condition in settings.json.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (a guarded key changed in staged settings.json)

set -uo pipefail

# Every git call below is capped via _lib_capped — see _lib.sh for the cap and its fallback behavior.
# On a machine lacking both timeout(1) and gtimeout(1), _lib_capped runs
# these git calls uncapped, so a stalled git (locked index, network mount)
# hangs this gate rather than degrading gracefully.

DENY_GATE_LABEL="settings session-keys"

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

# Only gate commands that contain a git commit invocation. Deliberately
# unchecked, matching this hook's own fail-open posture on the jq-absent
# path below: status 2 (could not determine) falls through the same "not
# gated, allow" path as status 1 (no match), rather than gaining a
# dedicated deny fork.
_lib_command_invokes_git_subcmd "$COMMAND" commit || exit 0

# Resolve the repo from the payload's cwd rather than this hook process's
# ambient cwd, matching require-plan-review.sh/require-code-review.sh.
CWD=$(printf '%s\n' "$INPUT" | _lib_jq -r '.cwd // empty' 2>/dev/null); [ -z "$CWD" ] && CWD="$PWD"

# Only proceed if inside a git repo.
if [ "$(_lib_capped git -C "$CWD" rev-parse --is-inside-work-tree 2>/dev/null)" != "true" ]; then
  exit 0
fi

SETTINGS_REPO_PATH="claude/.claude/settings.json"

# Check whether settings.json is staged at all.
if ! _lib_capped git -C "$CWD" diff --cached --name-only 2>/dev/null | grep -qF "$SETTINGS_REPO_PATH"; then
  exit 0
fi

# Diffs the staged version against origin/<default branch>; an unresolvable
# branch or missing file diffs against an empty baseline instead.
# Fail-CLOSED exception to this file's fail-open posture: an unresolvable
# default branch denies rather than allowing.
# Latency tradeoff: see docs/design-decisions.md's entry for
# _lib_resolve_default_branch (#54).
DEFAULT_BRANCH=$(_lib_resolve_default_branch "$CWD")
STAGED_CONTENT=$(_lib_capped git -C "$CWD" show :"$SETTINGS_REPO_PATH" 2>/dev/null)
if [ -z "$DEFAULT_BRANCH" ] || ! _lib_capped git -C "$CWD" show "origin/$DEFAULT_BRANCH:$SETTINGS_REPO_PATH" >/dev/null 2>&1; then
  MAIN_CONTENT=""
else
  MAIN_CONTENT=$(_lib_capped git -C "$CWD" show "origin/$DEFAULT_BRANCH:$SETTINGS_REPO_PATH" 2>/dev/null)
fi

# The keys holding one machine's own state, which must never ship as the
# config every stow user receives. A dotted key (e.g. "env.FOO") is guarded
# via path traversal, not a literal top-level match — see guarded_value below.
GUARDED_KEYS_JSON='[
  "model",
  "effortLevel",
  "skipAutoPermissionPrompt",
  "skipWorkflowUsageWarning",
  "theme",
  "tui",
  "env.CLAUDE_CODE_EFFORT_LEVEL",
  "env.ANTHROPIC_MODEL"
]'

# Name the guarded keys whose staged value differs from the default branch. Notes:
# - One jq call, not one per key: hooks fire on every matching tool call, so a
#   spawn-per-key loop would not hold the per-fire latency budget.
# - _lib_jq, not bare jq: a wedged jq would otherwise hang the gated commit
#   indefinitely, the same risk _lib_capped covers for git above.
# - guarded_value walks a dot-split path, so a nested key (e.g.
#   "env.CLAUDE_CODE_EFFORT_LEVEL") is guarded exactly like a top-level one —
#   including the null/false-vs-absent distinction — and a non-object
#   mid-path segment degrades to "not present" instead of erroring.
# - Content that does not parse degrades to {}, so keys the other side does
#   have still register as changed. Only a jq that cannot run at all yields no
#   names, and that path warns below rather than passing silently.
# shellcheck disable=SC2016 # single-quoted on purpose: $guarded/$staged/$main are jq --arg bindings, not shell variables; double-quoting would expand them in the shell before jq sees them. Bare `jq` suppresses this itself, but the _lib_jq wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
if ! CHANGED_KEYS=$(_lib_jq -rn \
  --argjson guarded "$GUARDED_KEYS_JSON" \
  --arg staged "$STAGED_CONTENT" \
  --arg main "$MAIN_CONTENT" \
  'def guarded_value($settings; $key):
     ($key | split(".")) as $path
     | reduce $path[] as $seg
         ({present: true, value: $settings};
          if .present and (.value | type) == "object" and (.value | has($seg))
          then {present: true, value: .value[$seg]}
          else {present: false, value: null}
          end)
     | if .present then [.value] else [] end;
   (($staged | fromjson?) // {}) as $staged_settings
   | (($main | fromjson?) // {}) as $main_settings
   | [ $guarded[]
       | . as $key
       | select(guarded_value($staged_settings; $key)
                != guarded_value($main_settings; $key)) ]
   | join(" ")' 2>/dev/null); then
  # Allow, matching this gate's fail-open posture, but say so — a silent
  # allow here is indistinguishable from a clean one, and leaves the engineer
  # believing a guard ran that did not.
  printf '%s\n' "guard-settings-session-keys: jq could not run (missing or timed out) — the settings-key guard did not evaluate this commit." >&2
  exit 0
fi

if [ -z "$CHANGED_KEYS" ]; then
  exit 0
fi

emit_deny "settings.json has machine-local or session-scoped keys changed — commit these only if intentional. The staged settings.json differs from ${DEFAULT_BRANCH:-the default branch} on: ${CHANGED_KEYS}. These keys hold one machine's own state, and several are written by Claude Code rather than by hand (model and effortLevel from /config, skipAutoPermissionPrompt when it records the permission-prompt preference), so committing them ships your local state as the shipped config for every user. Unstage the file (git restore --staged claude/.claude/settings.json) to allow the commit, or proceed only if this is a deliberate update."
