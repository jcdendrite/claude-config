#!/bin/bash
# hook-class: gate
# Gate: block every shape of `gh pr merge` issued by the AI agent.
# Enforces the repo-root CLAUDE.md rule "AI agents: don't merge your own PRs":
# CI passing is necessary but not sufficient — the engineer merges directly.
#
# Dispatch: no `if`-pattern in settings.json — intentional. `Bash(gh pr merge *)`
# would miss the bare `gh pr merge` (no-args) form; self-filtering is the only
# shape that covers both. Fires on every Bash call; fast-exits for non-merge cmds.
#
# Fail posture: fail-closed on JSON parse error (jq non-zero exit → deny),
# on jq missing/failed/hung (emit_deny's exit-2 fallback), AND on the
# _lib_command_invokes_tool_subcmd matcher itself returning "could not
# determine" (status 2 — sed/tr missing, killed, or errored). Because this
# hook has no `if` matcher and fires on every Bash call, and its `TOOL_NAME
# != Bash` / empty-`COMMAND` fast paths sit after
# `_lib_parse_tool_input_or_deny`, a missing jq denies every Bash call, not
# only merge attempts — the same posture every other gate hook shares.
# Fail-open only on missing/non-string command field (allow through — cannot
# confirm the call is a Bash merge attempt, so do not block). A missing
# tool_name does NOT fail open: _lib.sh's empty-TOOL_NAME check denies it.
#
# Known gaps (out of scope by design):
#   - `gh api repos/OWNER/REPO/pulls/N/merge` — the gh-api path to merge.
#     Different command shape; excluded by the implementation brief.
#   - `eval "gh pr merge..."`, `bash -c "gh pr merge..."` — subshell wrappers.
#     The hook inspects tool_input.command, not the expanded subshell content.
#     Claude Code agents operating in good faith do not use these forms.

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
  emit_deny "Blocked by gh-pr-merge gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked: could not parse tool-input JSON for gh-pr-merge gate."

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

[ -z "$COMMAND" ] && exit 0

# Fragment-and-command-word match via the shared gh-family matcher:
# Matches: `gh pr merge 291 --squash`, `(cd /repo && gh pr merge 291)`,
#          `gh pr merge; echo done`, `git push && gh pr merge 291`, bare
#          `gh pr merge`, `gh --repo o/r pr merge`, `/usr/bin/gh pr merge N`,
#          and `gh pr "merge"` (see docs/hooks.md's entry for this hook).
# Rejects: `echo "gh pr merge"` (command word resolves to `echo`, not `gh`),
#          `gh pr mergefoo` (word-split boundary, not a substring match).
_lib_command_invokes_tool_subcmd "$COMMAND" gh pr merge
GH_PR_MERGE_MATCH_STATUS=$?
if [ "$GH_PR_MERGE_MATCH_STATUS" -eq 0 ]; then
  emit_deny "Blocked: '${COMMAND:0:200}' would merge a PR. Per the repo-root CLAUDE.md rule \"AI agents: don't merge your own PRs\", an AI agent that opens a PR does not also merge it — CI passing is necessary but not sufficient. Surface the merge intent to the engineer and wait for their explicit \"merge it\" instruction. (The engineer can run the command directly via the ! shell escape.)"
  exit 0
fi
if [ "$GH_PR_MERGE_MATCH_STATUS" -ne 1 ]; then
  emit_deny "Blocked: could not determine whether '${COMMAND:0:200}' invokes gh pr merge (status ${GH_PR_MERGE_MATCH_STATUS}) — sed/tr may be missing, killed, or errored. Failing closed per this gate's documented fail-closed posture rather than letting an unscanned command bypass the self-merge block."
  exit 0
fi

exit 0
