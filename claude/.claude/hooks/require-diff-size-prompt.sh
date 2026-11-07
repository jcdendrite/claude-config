#!/usr/bin/env bash
# hook-class: gate
# tier-threat-model: cooperative
# Gate: deny a Bash `git commit` that brings the branch's cumulative diff against
# origin/<base> to 400 or more changed lines until the count falls below the
# threshold or the engineer answers `Proceed anyway`. A hook cannot call
# AskUserQuestion, so the deny has the agent ask. On `Proceed anyway` the agent
# runs ~/.claude/scripts/diff-size-ack.sh, which writes the per-branch latch this
# gate honors; this hook never writes it. Any commit made while a merge, rebase,
# cherry-pick or revert is paused, a `--continue` form included, is allowed unmeasured.
# The next ordinary commit is measured.
#
# The count is the cumulative diff /ready-for-review reviews, minus files under
# the repository root's .claude/plans/. _lib.sh's _lib_diff_size_gate_state owns
# the read sequence, the threshold and the count; the ack script runs the same
# function, so the two agree on the config dir, branch, latch path and count.
#
# This gate is a cost-and-quality interruption, not a security control. It has
# no kill switch, because diff-size-ack.sh releases it once per branch.
#
# Registered on the PreToolUse Bash matcher with no `if` condition; the hook
# filters on tool name and command itself, as enforce-marker-script-shape.sh does.
#
# The count reads the index at PreToolUse time. That equals the landed commit only
# because deny-invisible-commit-content.sh denies a chained `git add`, a `-a`
# commit and a pathspec commit.
# docs/hooks.md's deny-invisible-commit-content.sh bullet lists this gate as a
# dependent.
#
# Cost: one extra hook bootstrap on every Bash call, before the `*git*` prefilter;
# .claude/plans/hook-bootstrap-cost.md holds the measured per-hook bootstrap cost.
# Upper bound on wall time: _lib_diff_size_gate_state's header bound plus two capped
# _lib_jq calls (the payload parse and the deny encoding) at 7 seconds each (5-second
# cap plus the 2-second kill grace).
#
# Failure direction:
#   - Deny on payload failure (malformed JSON, empty stdin, non-object tool_input,
#     missing _lib.sh, missing jq), as every gate does.
#   - Allow on every state failure: an unresolvable config dir, repository or
#     branch, a detached HEAD, an unresolvable base, and a cap kill on any git call.
#   - Allow on a commit-parse tool failure: `sed` or `tr` missing from PATH.
#   - Deny again, writing nothing, until diff-size-ack.sh writes the branch's latch.
#     A deny another gate's reason hid therefore repeats on the next attempt.
#   - A harness hook timeout blocks the commit on Claude Code 2.1.295 or later; see
#     _lib_capped_for's header in _lib.sh.
#
# Roster bar: a review-only, plan-writer or code-writer agent running the bare ack
# command is denied here, before any repository read, because the ack script has no
# caller identity. The bar keeps roles consistent and is not a security control.
# The bar covers one spelling of the ack command, so
# `bash ~/.claude/scripts/diff-size-ack.sh`, an absolute-path form or a `cd . &&`
# prefix passes it. A subagent outside the roster is not barred.
#
# Known gaps, stated rather than left implicit:
#   - A branch based on a non-default branch with no stack key set counts against
#     the default branch and overcounts, with or without an open PR. One ack clears it.
#   - A stack key naming a parent with no origin/<parent> ref leaves the branch unmeasured.
#   - A stack key `_lib_configured_stack_base` finds unusable leaves the branch unmeasured,
#     and this hook discards the diagnostic the ack prints.
#   - The count reads origin/<base> as last fetched.
#   - A binary file counts zero lines.
#   - A file with the `-diff` or `binary` attribute counts zero lines, so a
#     repo-controlled attribute can zero a source file.
#   - The .claude/plans/ carve-out excludes every file type under it, not only Markdown.
#   - Plans kept anywhere other than the repository root's .claude/plans/ count.
#   - A file moved into or out of that directory counts as a whole-file add or delete.
#   - The count runs below the diff /ready-for-review reviews, and below the
#     additions-plus-deletions size the 400 edge was taken from, by the lines of
#     files under the repository root's .claude/plans/.
#   - A shallow clone whose history lacks the common ancestor leaves the branch
#     unmeasured, because `merge-base` fails.
#   - With no stack key set, a remote not named `origin` leaves the branch unmeasured,
#     because the default-branch guess finds no origin/* ref.
#   - With a stack key set, a remote not named `origin` leaves the branch unmeasured,
#     because `merge-base` fails on the missing refs/remotes/origin/<parent> ref.
#   - `git -C <dir> commit` and `cd <dir> && git commit` are measured in the payload
#     cwd's repository, not <dir>.
#   - A GIT_DIR, GIT_WORK_TREE or GIT_INDEX_FILE prefix on the commit redirects only
#     the commit, not the count.
#   - The count matches the landed commit only for the shapes
#     deny-invisible-commit-content.sh closes, so that hook's Known gaps apply here too.
#   - In a partial clone, a blob the count lacks leaves the commit unmeasured on git
#     that honors GIT_NO_LAZY_FETCH, and older git may fetch it under the count cap.
#   - With neither `timeout` nor `gtimeout` on PATH, every capped call runs uncapped,
#     so a stalled git call holds the commit until the harness's hook timeout.
#   - A count call the 30-second cap kills leaves the commit unmeasured. The next
#     commit measures again.
#   - A repo whose count always reaches the cap pays up to that wait on every commit
#     and is never gated.
#   - The latch records an approved run of the exact bare ack command. The harness
#     prompt shows the engineer the command, not their AskUserQuestion answer.
#   - The ack's `permissions.ask` entry covers one spelling of the ack command.
#     Whether it matches any other spelling is documented, not observed.
#   - Under `bypassPermissions` a spelling the entry misses writes the latch with no
#     prompt, and under auto mode the classifier decides.
#   - The deny text tells a non-roster subagent to report to its dispatcher instead.
#   - When the ack call is declined, or `dontAsk` mode denies it, the agent cannot
#     release the gate and the engineer runs the ack in their own terminal.
#   - In the documented flow the prompt comes after the /code-review round, because
#     /code-review writes its marker in the commit chain.
#   - A question asked in prose rather than through AskUserQuestion may be pushed past
#     by advance-past-commit-stall.sh when autonomous shipping is active.
#   - A headless or scheduled session stops at the deny and reports, because the deny
#     text says to. The ask prompt's headless behavior is untested.
#   - A branch name reused at the same checkout path within 30 days inherits the old
#     branch's latch.
#   - A latch written more than 30 days ago is swept only when an ack writes a new latch, so
#     a re-ask after 30 days from the ack is possible, not guaranteed.
#   - The gate's allows leave no trace, and only the ack's latch line records an
#     acknowledgment. A deny is countable from transcripts through denials.py's label
#     while they are retained. The label is shared, so the count mixes size denies with
#     roster-bar denies.
#   - The latch is not tamper-evident. The ack costs a prompt, but a direct write to
#     the plain file the gate honors releases the gate with no trace line and no
#     measurement, because enforce-marker-script-shape.sh's marker-shape guard covers
#     `*-markers/*` and `.*-active.d/*`, not `*-latch.d`.
#   - The base ref name is shape-checked against [A-Za-z0-9._/-] before it is printed,
#     and a name that fails prints as a placeholder. A name that passes prints verbatim
#     with no length bound. The default-branch arm takes the name from origin/HEAD's
#     target with no shape check of its own.

set -uo pipefail

DENY_GATE_LABEL="diff-size"

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
  # _lib.sh's _lib_emit_deny comment).
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh. For the recovery, see docs/hooks.md § \"Gate deadlock recovery\" in the claude-config repository; the user, not the agent, runs ./install.sh from the claude-config checkout in a separate terminal only if that runbook says so."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

# The command text is compared literally, so the tilde stays unexpanded.
# shellcheck disable=SC2088
ACK_COMMAND="~/.claude/scripts/diff-size-ack.sh"

# _command_is_bare_ack
# True iff COMMAND, minus leading and trailing ASCII whitespace, is exactly ACK_COMMAND.
# The ack's `permissions.ask` entry matches the same one spelling.
_command_is_bare_ack() {
  local LC_ALL=C
  local trimmed="$COMMAND"
  trimmed="${trimmed#"${trimmed%%[![:space:]]*}"}"
  trimmed="${trimmed%"${trimmed##*[![:space:]]}"}"
  [ "$trimmed" = "$ACK_COMMAND" ]
}

[ "$TOOL_NAME" = "Bash" ] || exit 0

if _command_is_bare_ack && _lib_is_no_gate_release_agent "$AGENT_TYPE"; then
  emit_deny "the '$AGENT_TYPE' agent cannot release the diff-size gate by running ${ACK_COMMAND}. Report this to your dispatcher, which asks the engineer."
  exit 0
fi

# Pure-bash test, so every other Bash call skips the commit parse's sed and tr forks.
[[ "$COMMAND" == *git* ]] || exit 0

# Status 2 (sed or tr missing from PATH) allows too, under this gate's allow-on-state-failure posture.
concludes_commit_status=0
_lib_command_concludes_commit "$COMMAND" || concludes_commit_status=$?
[ "$concludes_commit_status" -eq 0 ] || exit 0

[ -n "$CWD" ] || CWD="$PWD"

gate_state_status=0
gate_state_output=$(_lib_diff_size_gate_state "$CWD" 2>/dev/null) || gate_state_status=$?
[ "$gate_state_status" -eq 0 ] || exit 0

# The output's first line is the latch path, which the deny text never names; the second is "<count> <base>".
measurement="${gate_state_output#*$'\n'}"
changed_line_count="${measurement%% *}"
base_branch="${measurement#* }"

# The default-branch arm takes the base from origin/HEAD's target with no shape check, and the reason is read by the agent.
base_name_shape_status=0
_lib_stack_base_name_shape "$base_branch" || base_name_shape_status=$?
if [ "$base_name_shape_status" -eq 0 ]; then
  base_ref="origin/$base_branch"
else
  base_ref="the base branch"
fi

emit_deny "once this commit lands, the branch's diff against ${base_ref} holds ${changed_line_count} changed lines (additions plus deletions, not counting files under .claude/plans/), at or over the ${_LIB_DIFF_SIZE_PROMPT_THRESHOLD_LINES}-line threshold. The count is measured, not estimated. Stop and ask the engineer through AskUserQuestion whether to split now or proceed anyway, with exactly the options \`Split now\` and \`Proceed anyway\`. The question may cite an answer the engineer gave at plan time. Do not retry before they answer. On \`Proceed anyway\`: (1) state the count and base in a message before the call; (2) run exactly \`${ACK_COMMAND}\`, as its own Bash call, from this directory, in no other spelling; (3) re-run the denied command unchanged, chain included, because no part of it ran. If the same denial recurs after the ack ran, whatever its exit status, report it to the engineer instead of asking again. If the ack call is denied or declined, stop and report: do not re-run the ack in any other spelling, do not release the gate by any other means, and do not retry the commit. The engineer can run the ack from this directory in their own terminal. On \`Split now\`: do not retry; agree with the engineer which part of the work moves, and start that branch through the branch-management skill. If the engineer says the new branch stacks on this one, set \`git config branch.<new-branch>.gh-merge-base <this-branch>\`, the key /ready-for-review step 1 sets. This gate keeps denying commits here until the branch's count falls below the threshold or the engineer answers \`Proceed anyway\`. A session that cannot put the question to an engineer, such as a headless or scheduled run, stops and reports the count and base instead of retrying. A subagent reports this denial to its dispatcher instead."

exit 0
