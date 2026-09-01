#!/bin/bash
# hook-class: gate
# Gate: restricts _LIB_BASH_MUTATION_RESTRICTED_AGENTS members' Bash calls to
# a closed read-only/verification allowlist since Bash alone could mutate the
# tree despite no Edit/Write.
# require-review-orchestrator-agent-target.sh closes the nested-dispatch gap
# this leaves open — see docs/design-decisions.md §39 for the fuller
# rationale.
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
# it fail-closed. This matches deny-reviewer-tree-mutation.sh's identical
# handling of its own jq read. An unchecked read would leave AGENT_TYPE empty
# on a jq failure and fall straight through to allow. That is the one
# fail-open path in a file that denies on every other read failure.
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

# Closed verification-command allowlist:
# - Exactly the forms root CLAUDE.md's own Commands section names, plus
#   their worktree-relative (../../../.venv/bin/...) forms.
# - Matched against the WHOLE command, not per-fragment: the shellcheck form
#   is itself a pipe, and per-fragment splitting (used below for everything
#   else) would break it into two unrecognizable halves.
# - No chaining: a verification command combined with anything else via &&
#   falls through to the fragment-based check below.
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

# Canonical on-disk locations of the sanctioned tools.
# - $HOME, not _lib_config_dir: matches the invocation form every other
#   caller of these scripts hardcodes (~/.claude/scripts/<name>), used
#   regardless of whether CLAUDE_CONFIG_DIR is set. See plan-review/SKILL.md's
#   "marker.sh invocations stay hardcoded to ~/.claude/scripts/marker.sh" note.
# - A resolver failure (unset $HOME, no realpath/grealpath, and no existing
#   ancestor to walk to) leaves CANONICAL_MARKER_SH/CANONICAL_REVIEW_LEDGER_SH/
#   CANONICAL_ORCHESTRATOR_CHECKPOINT_SH empty. The checks below treat an
#   empty canonical path as "nothing can match," not as "skip this check."
# - CANONICAL_GIT is the one exception: see docs/design-decisions.md §39 for
#   why its bare-word `git` match is accepted without a realpath resolution.
CANONICAL_GIT=$(command -v git 2>/dev/null || true)
CANONICAL_MARKER_SH=""
CANONICAL_REVIEW_LEDGER_SH=""
CANONICAL_ORCHESTRATOR_CHECKPOINT_SH=""
_CANONICAL_MARKER_SH_ATTEMPTED=0
_CANONICAL_REVIEW_LEDGER_SH_ATTEMPTED=0
_CANONICAL_ORCHESTRATOR_CHECKPOINT_SH_ATTEMPTED=0

# True iff FRAGMENT's resolved command word is the real git binary: the bare
# word `git` (trusting the same PATH lookup the eventual command runs under)
# or an exact match on `command -v git`'s resolved path.
# Never a basename-only match: unlike _lib_fragment_invokes_git's denylist
# callers, a basename match here would grant full Bash privileges to any
# file named git anywhere in the tree (e.g. a PR-shipped ./vendor/git).
_fragment_invokes_canonical_git() {
  local fragment="$1" cmd
  cmd=$(_lib_fragment_command_word "$fragment")
  [[ -n "$cmd" && ( "$cmd" == "git" || ( -n "$CANONICAL_GIT" && "$cmd" == "$CANONICAL_GIT" ) ) ]]
}

# True iff FRAGMENT invokes sudo or doas anywhere. _lib_fragment_command_word
# treats both as transparent wrappers it walks past to reach the real
# command, correct for its denylist callers since `sudo rm -rf` must still
# resolve to "rm" to get caught. That's wrong for this hook's allowlist
# direction: walking past sudo/doas here would let `sudo git log` match the
# same canonical-git allow arm as an unprivileged `git log`, executing as
# root on any machine with passwordless sudo for git. Scans every word,
# not just the resolved command word, so a wrapper-of-a-wrapper form
# (`env sudo git log`) is caught too. Strips every quote character,
# backslash-escape, and ANSI-C/locale ($'...'/$"...") quote opener from each
# word via _lib_strip_word_quotes before matching, so a quoted,
# interior-spliced, backslash-escaped, or ANSI-C/locale-quoted wrapper name
# (`'sudo' git log`, `su'do' git log`, `\sudo git log`, `$'sudo' git log`) is
# caught directly rather than relying on _lib_fragment_command_word's own
# (equally quote-blind) wrapper resolution to fail this fragment closed for
# an unrelated reason.
_fragment_has_privilege_escalation_wrapper() {
  local fragment="$1" saved_opts=$- word stripped found=false
  set -f
  for word in $fragment; do
    _lib_strip_word_quotes "$word"
    stripped="$_LIB_STRIPPED_WORD"
    case "${stripped##*/}" in
      sudo|doas) found=true; break ;;
    esac
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  $found
}

# True iff FRAGMENT invokes `git grep --no-index`. --no-index turns git grep
# into a plain filesystem search with no repository-boundary restriction at
# all -- unlike an ordinary `git grep`, which git itself already scopes to
# this repo's tracked/working-tree content, so no separate path-argument
# check is needed there. --no-index makes this an unconditional
# arbitrary-file-read primitive regardless of what pathspec argument rides
# along with it, so it is denied outright rather than validated per-path.
# Strips every quote character, backslash-escape, and ANSI-C/locale
# ($'...'/$"...") quote opener from each word via _lib_strip_word_quotes
# before matching, so a quoted, interior-spliced, backslash-escaped, or
# ANSI-C/locale-quoted flag (`git grep -'-no-index'`, `git grep \--no-index`,
# `git grep $'--no-index'`) is caught directly rather than passing through as
# an unmatched word.
_fragment_has_git_grep_no_index_flag() {
  local fragment="$1" saved_opts=$- word found=false
  set -f
  for word in $fragment; do
    _lib_strip_word_quotes "$word"
    if [ "$_LIB_STRIPPED_WORD" = "--no-index" ]; then
      found=true
      break
    fi
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  $found
}

# Memoizes every realpath resolution this hook performs: both
# _resolve_canonical_script's ~/.claude/scripts/BASENAME lookups below and
# _resolve_fragment_cmd_path's per-fragment command-word lookups share one
# cache. The cache is keyed by each path's _lib_collapse_dot_segments-
# normalized string. A repeated or canonically-spelled command word therefore
# costs at most one _lib_realpath_m call, regardless of which of the two call
# sites asks for it first.
_RESOLVED_FRAGMENT_CMD_KEYS=()
_RESOLVED_FRAGMENT_CMD_VALUES=()
_RESOLVED_FRAGMENT_CMD_PATH=""
_RESOLVED_FRAGMENT_CMD_COUNT=0

# Bounds how many distinct paths this hook invocation will spend a
# _lib_realpath_m subprocess resolving, shared across
# _resolve_canonical_script and _resolve_fragment_cmd_path. Without this cap,
# an attacker-controlled fragment count -- each spelling the same canonical
# script differently in a way _lib_collapse_dot_segments's cache-key
# normalization cannot fold away -- would force one resolution per spelling.
# Set to the same value as enforce-marker-script-shape.sh's
# MARKER_WRITE_REALPATH_BUDGET cap on the same risk class.
# This budget composes with _lib_realpath_m's own unresolved_depth_budget
# per resolution, but the two together have no aggregate wall-clock ceiling
# across a single hook fire.
_FRAGMENT_CMD_RESOLVE_BUDGET=10

# _resolve_fragment_cmd_path EXPANDED_CMD
# Sets _RESOLVED_FRAGMENT_CMD_PATH to EXPANDED_CMD's realpath-resolved path
# and returns 0, or clears it and returns 1, on an unresolvable path. Sets a
# global rather than printing for the caller to capture via $(...): a
# command-substitution subshell would fork away this function's
# _RESOLVED_FRAGMENT_CMD_KEYS/_VALUES and _FRAGMENT_CMD_RESOLVE_BUDGET
# mutations before they ever reach the caller, silently defeating both the
# memoization and the budget cap. See the cache comment above for why this
# memoizes by _lib_collapse_dot_segments-normalized string rather than
# calling _lib_realpath_m directly. Two paths fail closed (denied by this
# allowlist gate) rather than raising or falling through:
# - A spelling seen after _FRAGMENT_CMD_RESOLVE_BUDGET is exhausted is
#   treated as unresolvable rather than spending another subprocess on it.
# - A path past _lib_collapse_dot_segments's own iteration cap (e.g. a
#   "/./"-segment flood) fails the same way, before reaching the resolve
#   budget.
_resolve_fragment_cmd_path() {
  local expanded="$1" i cache_key
  cache_key=$(_lib_collapse_dot_segments "$expanded") || { _RESOLVED_FRAGMENT_CMD_PATH=""; return 1; }
  for ((i = 0; i < _RESOLVED_FRAGMENT_CMD_COUNT; i++)); do
    if [ "${_RESOLVED_FRAGMENT_CMD_KEYS[$i]}" = "$cache_key" ]; then
      _RESOLVED_FRAGMENT_CMD_PATH="${_RESOLVED_FRAGMENT_CMD_VALUES[$i]}"
      [ -n "$_RESOLVED_FRAGMENT_CMD_PATH" ] || return 1
      return 0
    fi
  done
  if [ "$_FRAGMENT_CMD_RESOLVE_BUDGET" -gt 0 ]; then
    _FRAGMENT_CMD_RESOLVE_BUDGET=$((_FRAGMENT_CMD_RESOLVE_BUDGET - 1))
    _RESOLVED_FRAGMENT_CMD_PATH=$(_lib_realpath_m "$cache_key" 2>/dev/null) || _RESOLVED_FRAGMENT_CMD_PATH=""
  else
    _RESOLVED_FRAGMENT_CMD_PATH=""
  fi
  _RESOLVED_FRAGMENT_CMD_KEYS+=("$cache_key")
  _RESOLVED_FRAGMENT_CMD_VALUES+=("$_RESOLVED_FRAGMENT_CMD_PATH")
  _RESOLVED_FRAGMENT_CMD_COUNT=$((_RESOLVED_FRAGMENT_CMD_COUNT + 1))
  [ -n "$_RESOLVED_FRAGMENT_CMD_PATH" ] || return 1
}

# Repo root this hook's git checks scope path-bearing flags against.
# Resolved lazily (only when a git fragment actually carries -C/--git-dir/
# --work-tree/--namespace/--super-prefix) by _resolve_repo_root_once below,
# so an ordinary git fragment with none of those flags never pays for the
# `git rev-parse --show-toplevel` subprocess.
REPO_ROOT=""
_REPO_ROOT_RESOLVE_ATTEMPTED=0
_REPO_ROOT_RESOLVE_FAILED=0
_resolve_repo_root_once() {
  if [ "$_REPO_ROOT_RESOLVE_ATTEMPTED" -eq 0 ]; then
    _REPO_ROOT_RESOLVE_ATTEMPTED=1
    if ! REPO_ROOT=$(_lib_resolve_repo_root "require-review-orchestrator-bash.sh" 2>/dev/null); then
      REPO_ROOT=""
      _REPO_ROOT_RESOLVE_FAILED=1
    fi
  fi
}

# True iff FRAGMENT carries -C, --git-dir, --work-tree, --namespace, or
# --super-prefix with an argument that resolves outside REPO_ROOT. Each of
# these retargets which repository (or ref namespace) git operates against,
# independent of the subcommand. A subcommand-word-only allowlist check
# would treat `git -C /any/readable/path log` as the same safe read-only
# `log` it would be against this repo, when it actually reads whatever repo
# sits at that other path. Handles both the `--flag value`/`-C value` and
# `--flag=value`/`-Cvalue` forms. Fails closed (denied) if REPO_ROOT itself
# can't be resolved, or if a flag's argument can't be realpath-resolved.
# Strips every quote character, backslash-escape, and ANSI-C/locale
# ($'...'/$"...") quote opener from each word via _lib_strip_word_quotes
# before matching the flag literal or capturing its argument, so an
# interior-spliced quote, backslash-escape, or ANSI-C/locale-quoted opener
# (e.g. `git --git-di'r=/outside/path/.git' log`, `git \--git-dir=/outside
# log`, `git $'--git-dir=/outside' log`) is read as the flag/argument bash's
# own quote removal would reconstruct.
_fragment_has_git_path_flag_outside_repo_root() {
  local fragment="$1" saved_opts=$- past_git=false word stripped target found=false want_next=false
  set -f
  for word in $fragment; do
    if ! $past_git; then
      if [[ "$word" == "git" || "$word" == */git ]]; then
        past_git=true
      fi
      continue
    fi
    _lib_strip_word_quotes "$word"
    stripped="$_LIB_STRIPPED_WORD"
    target=""
    if $want_next; then
      want_next=false
      target="$stripped"
    else
      case "$stripped" in
        -C|--git-dir|--work-tree|--namespace|--super-prefix) want_next=true ;;
        --git-dir=*|--work-tree=*|--namespace=*|--super-prefix=*) target="${stripped#*=}" ;;
        -C?*) target="${stripped:2}" ;;
      esac
    fi
    if [ -n "$target" ]; then
      _resolve_repo_root_once
      if [ "$_REPO_ROOT_RESOLVE_FAILED" -eq 1 ] || ! _resolve_fragment_cmd_path "$target"; then
        found=true
        break
      fi
      case "$_RESOLVED_FRAGMENT_CMD_PATH" in
        "$REPO_ROOT" | "$REPO_ROOT"/*) ;;
        *) found=true; break ;;
      esac
    fi
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  $found
}

# _resolve_canonical_script BASENAME
# Realpath-resolves ~/.claude/scripts/BASENAME into its matching CANONICAL_*
# global the first time BASENAME is actually seen in the command, then
# caches the result (a failed resolution included) behind its own
# _ATTEMPTED flag. Routes through _resolve_fragment_cmd_path rather than
# calling _lib_realpath_m directly, so a fragment that spells the invocation
# exactly the canonical way hits the same cache entry instead of spending a
# second subprocess resolving the identical literal path.
_resolve_canonical_script() {
  case "$1" in
    marker.sh)
      if [ "$_CANONICAL_MARKER_SH_ATTEMPTED" -eq 0 ]; then
        _CANONICAL_MARKER_SH_ATTEMPTED=1
        if [ -n "${HOME:-}" ] && _resolve_fragment_cmd_path "${HOME%/}/.claude/scripts/marker.sh"; then
          CANONICAL_MARKER_SH="$_RESOLVED_FRAGMENT_CMD_PATH"
        fi
      fi
      ;;
    review-ledger.sh)
      if [ "$_CANONICAL_REVIEW_LEDGER_SH_ATTEMPTED" -eq 0 ]; then
        _CANONICAL_REVIEW_LEDGER_SH_ATTEMPTED=1
        if [ -n "${HOME:-}" ] && _resolve_fragment_cmd_path "${HOME%/}/.claude/scripts/review-ledger.sh"; then
          CANONICAL_REVIEW_LEDGER_SH="$_RESOLVED_FRAGMENT_CMD_PATH"
        fi
      fi
      ;;
    orchestrator-checkpoint.sh)
      if [ "$_CANONICAL_ORCHESTRATOR_CHECKPOINT_SH_ATTEMPTED" -eq 0 ]; then
        _CANONICAL_ORCHESTRATOR_CHECKPOINT_SH_ATTEMPTED=1
        if [ -n "${HOME:-}" ] && _resolve_fragment_cmd_path "${HOME%/}/.claude/scripts/orchestrator-checkpoint.sh"; then
          CANONICAL_ORCHESTRATOR_CHECKPOINT_SH="$_RESOLVED_FRAGMENT_CMD_PATH"
        fi
      fi
      ;;
  esac
}

# True iff FRAGMENT's resolved command word, tilde-expanded and
# realpath-normalized, is byte-identical to CANONICAL_MARKER_SH,
# CANONICAL_REVIEW_LEDGER_SH, or CANONICAL_ORCHESTRATOR_CHECKPOINT_SH --
# same rationale as _fragment_invokes_canonical_git above: a basename-suffix
# match would let a same-named script shipped elsewhere in the tree satisfy
# this allowlist branch.
# The basename switch below is a cheap gate before ever calling
# _lib_realpath_m, and _resolve_canonical_script/_resolve_fragment_cmd_path
# share one memoized resolution per distinct normalized path, so a
# many-fragment command pays for at most one resolution per distinct
# normalized spelling, not one per fragment.
_fragment_invokes_canonical_script() {
  local fragment="$1" cmd basename expanded resolved
  cmd=$(_lib_fragment_command_word "$fragment")
  [ -n "$cmd" ] || return 1
  basename="${cmd##*/}"
  case "$basename" in
    marker.sh | review-ledger.sh | orchestrator-checkpoint.sh) ;;
    *) return 1 ;;
  esac
  _resolve_canonical_script "$basename"
  # ${HOME:-}, not a bare $HOME: this runs unconditionally (no [ -n
  # "${HOME:-}" ] guard around this function), and set -uo pipefail turns a
  # bare unset-$HOME reference into a hard crash instead of the intended
  # fail-closed deny.
  expanded="${cmd/#\~/${HOME:-}}"
  _resolve_fragment_cmd_path "$expanded" || return 1
  resolved="$_RESOLVED_FRAGMENT_CMD_PATH"
  [ -n "$CANONICAL_MARKER_SH" ] && [ "$resolved" = "$CANONICAL_MARKER_SH" ] && return 0
  [ -n "$CANONICAL_REVIEW_LEDGER_SH" ] && [ "$resolved" = "$CANONICAL_REVIEW_LEDGER_SH" ] && return 0
  [ -n "$CANONICAL_ORCHESTRATOR_CHECKPOINT_SH" ] && [ "$resolved" = "$CANONICAL_ORCHESTRATOR_CHECKPOINT_SH" ] && return 0
  return 1
}

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

# Caps fragments checked per fire at 30 (a generous margin over legitimate
# use) so an attacker-controlled fragment count can't force one
# _fragment_has_unsafe_redirect sed spawn per fragment with no ceiling.
_FRAGMENT_COUNT_BUDGET=30

SPLIT_FRAGMENTS="$(_lib_split_fragments "$COMMAND")"
FRAGMENT_COUNT=0
while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue
  FRAGMENT_COUNT=$((FRAGMENT_COUNT + 1))
done <<< "$SPLIT_FRAGMENTS"

if [ "$FRAGMENT_COUNT" -gt "$_FRAGMENT_COUNT_BUDGET" ]; then
  emit_deny "Blocked by review-orchestrator Bash gate: command splits into $FRAGMENT_COUNT fragments, over this hook's $_FRAGMENT_COUNT_BUDGET-fragment cap. $SANCTIONED_ALTERNATIVE"
  exit 0
fi

# Tracks whether any non-empty fragment was actually evaluated. This is a
# deny-by-default allowlist, so an empty or malformed COMMAND -- which yields
# zero fragments to check -- must deny rather than fall through to the
# unconditional allow at the end; there is nothing here to have sanctioned it.
SAW_FRAGMENT=0

# Denies and exits on the first non-matching fragment rather than collecting
# all violations -- load-bearing for the composed depth-cap plus
# resolve-budget DoS bound above; a refactor toward reporting all violations
# before denying would need to re-derive that bound.
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

  if _fragment_has_privilege_escalation_wrapper "$fragment"; then
    emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' invokes sudo or doas -- either would run the wrapped command with elevated privileges, escaping this hook's read-only-and-unprivileged invariant. $SANCTIONED_ALTERNATIVE"
    exit 0
  fi

  if _fragment_invokes_canonical_git "$fragment"; then
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
    if _fragment_has_git_path_flag_outside_repo_root "$fragment"; then
      emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' carries a git flag (-C, --git-dir, --work-tree, --namespace, or --super-prefix) pointing outside this repo, or that argument could not be resolved -- these retarget which repository git operates against, regardless of subcommand. $SANCTIONED_ALTERNATIVE"
      exit 0
    fi
    subcmd=$(_lib_extract_git_subcmd "$fragment")
    if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
      emit_deny "Blocked by review-orchestrator Bash gate: 'git $subcmd' is not a read-only git subcommand. $SANCTIONED_ALTERNATIVE"
      exit 0
    fi
    if [ "$subcmd" = "grep" ] && _fragment_has_git_grep_no_index_flag "$fragment"; then
      emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' carries git grep's --no-index flag, which searches the filesystem directly with no repository-boundary restriction at all. $SANCTIONED_ALTERNATIVE"
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
  if _fragment_invokes_canonical_script "$fragment"; then
    continue
  fi

  emit_deny "Blocked by review-orchestrator Bash gate: '$fragment' is not on the closed allowlist (read-only git subcommands, marker.sh/review-ledger.sh/orchestrator-checkpoint.sh, or this repo's exact verification commands). $SANCTIONED_ALTERNATIVE"
  exit 0
# <<< here-string, not < <(...) process substitution: SPLIT_FRAGMENTS (the
# same _lib_split_fragments output the fragment-count check above already
# captured) has no trailing newline for a single, unsplit fragment, and a
# process-substitution `read` returns non-zero (loop body never runs) on a
# final line with no newline delimiter. `<<<` re-adds exactly one, guaranteeing
# the last fragment is newline-terminated. Mirrors deny-reviewer-tree-mutation.sh's
# `<<< "$(_lib_split_fragments ...)"` usage, minus the redundant re-split.
done <<< "$SPLIT_FRAGMENTS"

if [ "$SAW_FRAGMENT" -eq 0 ]; then
  emit_deny "Blocked by review-orchestrator Bash gate: empty or unrecognized command. $SANCTIONED_ALTERNATIVE"
  exit 0
fi

exit 0
