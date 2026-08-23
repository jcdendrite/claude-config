#!/bin/bash
# hook-class: gate
# Gate: restricts _LIB_BASH_MUTATION_RESTRICTED_AGENTS members' Bash calls to
# a closed read-only/verification allowlist since Bash alone could mutate the
# tree despite no Edit/Write; require-review-orchestrator-agent-target.sh
# closes the nested-dispatch gap this leaves open — see
# docs/design-decisions.md §29 for the fuller rationale.
#
# This hook is an allowlist (deny-by-default): a standalone `export
# VAR=value` fragment is never on the allowlist below, so it already denies
# without needing _lib_fragment_is_bare_env_assignment — that helper exists
# for deny-reviewer-tree-mutation.sh's denylist model, not this one.
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
  # False positive: shellcheck can't model this stub-then-override
  # redefinition (resolves correctly at call time); disabling rather than
  # restructuring preserves bootstrap coverage for a failed _lib.sh source.
  # shellcheck disable=SC2218
  emit_deny "Blocked by review-orchestrator Bash gate: could not source _lib.sh — hook cannot evaluate Bash restriction safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by review-orchestrator Bash gate: could not parse tool-input JSON. Refusing to evaluate the Bash restriction under malformed input."

# Filter by tool name here rather than relying on the settings.json matcher
# alone — matches require-skill-review.sh's stated precedent that the "if"
# field is a hint only.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# .agent_type is the trust-boundary field this whole gate hinges on, so read
# it fail-closed — matching deny-reviewer-tree-mutation.sh's identical
# handling of its own jq read. An unchecked read would leave AGENT_TYPE empty
# on a jq failure and fall straight through to allow: the one fail-OPEN path
# in a file that denies on every other read failure.
if ! AGENT_TYPE=$(printf '%s\n' "$INPUT" | _lib_jq -r '.agent_type // empty' 2>/dev/null); then
  emit_deny "Blocked by review-orchestrator Bash gate: could not read .agent_type from the tool payload — refusing to evaluate the Bash restriction under an unreadable trust-boundary field."
  exit 0
fi

# Fast common-path exit, BEFORE any command-text work: every agent type
# outside the closed restricted set (the main session, code-writer,
# general-purpose, every reviewer persona, or any other agent) passes
# through unconditionally regardless of command.
_lib_is_bash_mutation_restricted_agent "$AGENT_TYPE" || exit 0

SANCTIONED_ALTERNATIVE="review-orchestrator's Bash calls are restricted to read-only git subcommands, the marker.sh/review-ledger.sh/orchestrator-checkpoint.sh helper scripts, and this repo's own verification commands (pytest/ruff/shellcheck). Any step that requires changing repository content — applying a review fix, writing a non-marker file, running a formatter — must be satisfied by dispatching code-writer instead of running it directly."

# Closed verification-command allowlist: exactly the forms root CLAUDE.md's
# own Commands section names, plus their worktree-relative
# (../../../.venv/bin/...) forms — matched against the WHOLE command, not
# per-fragment, since the shellcheck form is itself a pipe and per-fragment
# splitting (used below for everything else) would break it into two
# unrecognizable halves. No chaining: a verification command combined with
# anything else via && falls through to the fragment-based check below.
case "$COMMAND" in
  '.venv/bin/pytest claude/.claude/') exit 0 ;;
  '.venv/bin/ruff check claude/.claude/') exit 0 ;;
  'scripts/list-shell-files.sh | xargs -0 .venv/bin/shellcheck') exit 0 ;;
  '../../../.venv/bin/pytest claude/.claude/') exit 0 ;;
  '../../../.venv/bin/ruff check claude/.claude/') exit 0 ;;
  'scripts/list-shell-files.sh | xargs -0 ../../../.venv/bin/shellcheck') exit 0 ;;
esac

# Strict read-only git subcommand alternation, built once from the single
# source of truth in _lib.sh: this hook's invariant is zero git-state
# mutation and zero network egress, stricter than _lib_readonly_git_subcmds's
# working-tree-race-safety invariant (which still permits branch/tag/
# symbolic-ref/fetch/remote/ls-remote).
ALLOWED_SUBCMDS=()
while IFS= read -r subcmd; do
  ALLOWED_SUBCMDS+=("$subcmd")
done < <(_lib_strict_readonly_git_subcmds)
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

# True iff $1 carries a redirect (`<`, `>`, `>>`) to anything other than
# /dev/null. A redirect fragment is a single unsplit unit under
# _lib_split_fragments (redirection isn't one of its split points), so
# without this check a fragment whose leading word matches an allowed git
# subcommand or helper script -- e.g. '~/.claude/scripts/review-ledger.sh
# show > src/tracked_file.py' -- would pass the command-word checks below
# untouched while actually truncating an arbitrary tracked file. The
# trailing `2>/dev/null` form enforce-marker-script-shape.sh itself blesses
# is exempted by stripping every occurrence (any fd, either `>` or `>>`)
# before testing what remains. The exemption is anchored to end-of-token
# (whitespace or end-of-string immediately after `/dev/null`) rather than a
# bare substring match, so `/dev/nullx` or a path-traversal suffix after
# `/dev/null` is never silently exempted.
_fragment_has_unsafe_redirect() {
  local fragment="$1" stripped
  stripped=$(printf '%s' "$fragment" | sed -E 's/[0-9]*>>?\/dev\/null([[:space:]]|$)/\1/g')
  [[ "$stripped" == *'<'* || "$stripped" == *'>'* ]]
}

# Tracks whether any non-empty fragment was actually evaluated. This is a
# deny-by-default allowlist, so an empty or malformed COMMAND -- which yields
# zero fragments to check -- must deny rather than fall through to the
# unconditional allow at the end; there is nothing here to have sanctioned it.
SAW_FRAGMENT=0

while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue
  SAW_FRAGMENT=1

  if _fragment_has_unsafe_redirect "$fragment"; then
    emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' redirects output to something other than /dev/null. $SANCTIONED_ALTERNATIVE"
    exit 0
  fi

  # Applies to every branch below (git AND the helper-script allowlist), not
  # just git: a leading env-var assignment still takes effect in the same
  # shell once the command actually runs, even though the helper-script
  # branch's tool-name match deliberately skips past it -- e.g.
  # CLAUDE_CONFIG_DIR=/tmp/x redirecting where marker.sh writes.
  if _lib_fragment_has_leading_env_assignment "$fragment"; then
    emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' begins with an environment-variable assignment -- CLAUDE_CONFIG_DIR/HOME and similar can redirect where a sanctioned helper script (marker.sh/review-ledger.sh/orchestrator-checkpoint.sh) reads or writes, and git's own env-based config-injection mechanism applies the same way. $SANCTIONED_ALTERNATIVE"
    exit 0
  fi

  if _lib_fragment_invokes_git "$fragment"; then
    if _lib_fragment_has_command_invoking_git_flag "$fragment"; then
      emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' carries a git flag (-c, --config-env, -O/--open-files-in-pager, --ext-diff, or --textconv) that can exec an arbitrary command regardless of subcommand. $SANCTIONED_ALTERNATIVE"
      exit 0
    fi
    if _lib_fragment_has_git_write_target_flag "$fragment"; then
      emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' carries a git flag (--output/--output-directory) that writes the command's own output to a caller-chosen filesystem path, with no shell redirect character for the redirect check above to catch. $SANCTIONED_ALTERNATIVE"
      exit 0
    fi
    if _lib_fragment_has_env_assignment_before_git "$fragment"; then
      emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' carries an environment-variable assignment before the git word -- git's GIT_CONFIG_COUNT/GIT_CONFIG_KEY_<n>/GIT_CONFIG_VALUE_<n> mechanism can set arbitrary config (e.g. diff.external) this way with no matching CLI flag. $SANCTIONED_ALTERNATIVE"
      exit 0
    fi
    subcmd=$(_lib_extract_git_subcmd "$fragment")
    if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
      emit_deny "Blocked by review-orchestrator Bash gate: 'git $subcmd' is not a read-only git subcommand. $SANCTIONED_ALTERNATIVE"
      exit 0
    fi
    continue
  fi

  # Exact-path invocations of the three sanctioned helper scripts. marker.sh's
  # own shape and gate-release authority are separately enforced by
  # enforce-marker-script-shape.sh; review-ledger.sh and
  # orchestrator-checkpoint.sh have no tree-mutation capability at all (both
  # are append-only bookkeeping scripts), so any invocation of either is safe
  # to allow here.
  if _lib_fragment_invokes_tool "$fragment" "marker.sh" \
    || _lib_fragment_invokes_tool "$fragment" "review-ledger.sh" \
    || _lib_fragment_invokes_tool "$fragment" "orchestrator-checkpoint.sh"; then
    continue
  fi

  emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' is not on the closed allowlist (read-only git subcommands, marker.sh/review-ledger.sh/orchestrator-checkpoint.sh, or this repo's exact verification commands). $SANCTIONED_ALTERNATIVE"
  exit 0
# <<< here-string, not < <(...) process substitution: _lib_split_fragments
# emits no trailing newline for a single, unsplit fragment, and a
# process-substitution `read` returns non-zero (loop body never runs) on a
# final line with no newline delimiter. `$(...)` command substitution strips
# any trailing newline and `<<<` re-adds exactly one, guaranteeing the last
# fragment is newline-terminated. Mirrors deny-reviewer-tree-mutation.sh's
# identical `<<< "$(_lib_split_fragments ...)"` usage.
done <<< "$(_lib_split_fragments "$COMMAND")"

if [ "$SAW_FRAGMENT" -eq 0 ]; then
  emit_deny "Blocked by review-orchestrator Bash gate: empty or unrecognized command. $SANCTIONED_ALTERNATIVE"
  exit 0
fi

exit 0
