#!/bin/bash
# hook-class: gate
# tier-threat-model: cooperative, untrusted-input, irreversible
# Gate: confines a plan-writer agent's file writes (see _lib.sh's
# _LIB_PLAN_WRITER_AGENTS) to existing plan files directly under this
# repository's .claude/plans/.
# A plan-writer agent reads repository files that can carry content from outside the session, so steered input is in scope.
# A bypass edits the dispatching session's uncommitted tree.
# An unparseable payload denies every caller, because the payload is parsed before the agent type is known.
# For a plan-writer, an empty, relative, or newline-containing path, an empty cwd, a failed git or realpath call, or a missing target denies.
# Write and MultiEdit always deny for a plan-writer; only Edit can be allowed.
# Registered without an `if` condition, because `if` cannot read agent identity.
# This hook filters on tool name and `.agent_type` itself.
#
# Known gaps (what this model does NOT close):
#   - Any plan file under .claude/plans/ is writable, not only the one the
#     dispatch named.
#     An edit to a committed plan re-arms the plan-review gate.
#     An edit to another untracked plan goes undetected and has no git
#     recovery.
#   - Any part of an allowed plan file is writable.
#     This hook does not detect a change above `## Approach`.
#     The dispatching /plan-it session does.
#   - The dispatch mode is invisible to this hook, so it allows a
#     consult-mode Edit to a plan file.
#   - The target can be swapped between this check and the tool's write.
#   - A hard link under .claude/plans/ to a file elsewhere, created
#     beforehand, passes the path check.
#   - A symlinked .claude/plans directory causes a false deny.
#     The agent returns its sections as text.
#   - A nested-worktree plan reached from the main tree's cwd causes a false
#     deny.
#     The agent returns its sections as text.
#   - A payload without `.agent_type` reads as a non-plan-writer and passes.
#     enforce-marker-script-shape.sh's header records the same dependency on
#     the harness's identity fields.
#   - This hook does not run where its file is not linked into the config
#     directory.
#   - A session that loaded its hook registrations before this hook was
#     added may not run it.
#   - Writes by the main session, and by agents outside the plan-writer
#     registry, are out of scope.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny

set -uo pipefail

DENY_GATE_LABEL="plan-writer-scope"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
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

case "$TOOL_NAME" in
  Write|Edit|MultiEdit) ;;
  *) exit 0 ;;
esac

# Fast common-path exit: every caller outside the plan-writer registry,
# including the main session (empty AGENT_TYPE), passes through.
_lib_is_plan_writer_agent "$AGENT_TYPE" || exit 0

PLAN_WRITER_SCOPE_RULE="a plan-writer agent may edit only an existing plan file directly under this repository's .claude/plans/; return your sections as text for the dispatching session to insert."

if [ "$TOOL_NAME" != "Edit" ]; then
  emit_deny "$PLAN_WRITER_SCOPE_RULE $TOOL_NAME is not allowed; only Edit is."
  exit 0
fi

if [[ "$FILE_PATH" != /* ]]; then
  emit_deny "$PLAN_WRITER_SCOPE_RULE The target path '$FILE_PATH' is empty or not absolute."
  exit 0
fi

# Command substitution strips a trailing newline from the resolved path, so the checks below would test a different name than the tool opens.
if [[ "$FILE_PATH" == *$'\n'* ]]; then
  emit_deny "$PLAN_WRITER_SCOPE_RULE The target path contains a newline."
  exit 0
fi

if [ -z "$CWD" ]; then
  emit_deny "$PLAN_WRITER_SCOPE_RULE The payload carries no .cwd to resolve the repository from."
  exit 0
fi

# Repo root comes from the payload's cwd, not this process's ambient cwd.
# The subshell unsets git's repo-selection variables so an inherited GIT_DIR
# cannot point the lookup at another repository.
REPO_ROOT=$(
  unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
  _lib_capped git -C "$CWD" rev-parse --show-toplevel 2>/dev/null
)
if [ -z "$REPO_ROOT" ]; then
  emit_deny "$PLAN_WRITER_SCOPE_RULE The repository root could not be resolved from cwd '$CWD'."
  exit 0
fi

if ! REAL_REPO=$(_lib_realpath_m "$REPO_ROOT") || ! REAL_TARGET=$(_lib_realpath_m "$FILE_PATH"); then
  emit_deny "$PLAN_WRITER_SCOPE_RULE The path '$FILE_PATH' could not be resolved."
  exit 0
fi

# Existence check: the plan-review gate's own exemption does not need one,
# but an Edit that names a missing file must not be allowed to proceed.
# The stat is uncapped because the capped realpath above already stat-ed this path.
if [[ ! -f "$REAL_TARGET" ]]; then
  emit_deny "$PLAN_WRITER_SCOPE_RULE '$FILE_PATH' is not an existing file."
  exit 0
fi

if ! _lib_is_repo_plan_file "$REAL_REPO" "$REAL_TARGET"; then
  emit_deny "$PLAN_WRITER_SCOPE_RULE '$FILE_PATH' is not a plan file under this repository."
  exit 0
fi

exit 0
