#!/bin/bash
# hook-class: gate
# Gate: stop Claude Code itself from performing an unsupported move/rename of
# this claude-config checkout — moving or renaming the repo root breaks every
# stow symlink under ~/.claude/ and ~/.local/bin/ at once, with nothing left
# running that could detect or explain the failure. Denies any `mv` or
# `rsync --remove-source-files`-shaped Bash fragment whose resolved source
# argument is this repo's root or an ancestor of it, and points at
# `relocate-claude-config` — the supported command that unstows, moves, and
# re-stows safely — instead.
#
# Self-locates the protected repo root via `readlink -f "$0"` on the hook's
# own physical (post-symlink) path rather than `git rev-parse` on cwd, since
# this hook can fire from a Bash call made in any repo, not just this one.
#
# Threat framing: this is a best-effort guard against the common
# literal-path case, not a hard security boundary — closing it fully would
# require the same cwd-threading machinery require-worktree-for-git-writes.sh
# needed parse-git-command.py for. The actual security investment against a
# semi-trusted destination argument lives on the destination side, in
# relocate-claude-config.sh's own validation — see that script's header.
#
# Known gaps (what this model does NOT close):
#   - A source indirected through a shell variable, command substitution, or
#     a preceding `cd` (which shifts the relative-path base this hook can't
#     thread) is not resolved and FAILS OPEN (allow) — denying every
#     unresolvable source would over-deny ordinary, repo-unrelated mv/rsync
#     usage. Worse than "breaks stow": an obfuscated `mv` never invokes
#     relocate-claude-config.sh either, so its destination-side validation
#     never runs — the repo can land anywhere, unchecked.
#   - Equivalent-relocation forms this pattern-match doesn't cover at all:
#     `cp -r ... && rm -rf ...`, `python3 -c "os.rename(...)"`, `ditto` plus
#     a delete, or a GUI/Finder move — none of these are closable by a
#     Bash-command-pattern hook.
#   - Same alias/wrapper-script indirection gap as deny-reviewer-tree-
#     mutation.sh (which shares _lib_fragment_command_word's word-scan): a
#     command reached only through an alias, a wrapper script, or a nested
#     shell boundary this scan never executes (`bash -c "mv ..."`) is
#     undecidable at this level. A command name quoted directly in $COMMAND
#     (`'mv' src dst`) IS caught — $COMMAND is quote-stripped before
#     splitting into fragments.
#   - `mv`/`rsync` flags that consume the following word as a value (e.g.
#     `-t DIR`) are not specially recognized, so a flag's value word could be
#     misjudged as a source. This can only over-deny, never miss a real
#     relocation, so it is left unhandled.
#   - COMMAND_UNQUOTED's sed/tr strip failure fails closed: its exit status
#     is checked and denies with an explicit message rather than falling
#     through to this hook's normal "no relocation matched" allow path.

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
  emit_deny "Blocked by repo-relocation hook: could not source _lib.sh — hook cannot evaluate relocation discipline safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by repo-relocation hook: could not parse tool-input JSON. Refusing to evaluate relocation discipline under malformed input."

[ "$TOOL_NAME" = "Bash" ] || exit 0

# Self-locate the protected repo root from this hook's own physical path —
# works whether invoked via the ~/.claude/hooks/ stow symlink or directly
# from the checkout (as the test suite does), since readlink -f resolves
# through any symlink either way.
HOOK_PHYSICAL_PATH=$(readlink -f -- "$0" 2>/dev/null)
REPO_ROOT="${HOOK_PHYSICAL_PATH%/claude/.claude/hooks/deny-repo-relocation.sh}"
# Empty (readlink -f failed) or unchanged (the suffix strip found no match,
# meaning this hook's own file was renamed or relocated within its package)
# both mean the protected root cannot be trusted — nothing to enforce.
if [ -z "$REPO_ROOT" ] || [ "$REPO_ROOT" = "$HOOK_PHYSICAL_PATH" ]; then
  exit 0
fi

# The Bash tool_input's .cwd is where the command would actually run;
# relative mv/rsync source arguments resolve against it, not this hook
# process's own $PWD. Falls back to $PWD when absent (mirrors
# require-worktree-for-git-writes.sh's identical CWD read).
CWD=$(_lib_jq -r '.cwd // empty' <<< "$INPUT" 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# Quote-stripped so an adjacent-quote split (`'mv' "$REPO_ROOT" /tmp`) can't
# dodge the word-walk detectors below — same helper as
# deny-network-installs.sh. A quoted source path must still resolve via
# readlink -f; unstripped, its literal quote characters fail resolution
# and fall through to this hook's documented fail-open. Checked and
# fail-closed, matching deny-invisible-commit-content.sh's own
# COMMAND_UNQUOTED computation.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
COMMAND_UNQUOTED_EXIT=$?
if [ "$COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "Blocked by repo-relocation hook: could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned mv/rsync."
  exit 0
fi

# Given a fragment already confirmed to invoke mv/rsync, print every
# positional (non-flag) argument after the command word, one per line,
# EXCLUDING THE LAST — mv/rsync take source(s) then a destination, and the
# destination is judged by relocate-claude-config.sh, not here. Mirrors
# _lib_fragment_command_word's env-var/runner-skip resolution but duplicated
# rather than shared, since that function returns only the command word, not
# the words after it.
_relocation_positional_sources() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local word past_cmd=false expect_after_runner=false
  local -a positionals=()
  for word in $fragment; do
    if ! $past_cmd; then
      if [[ "$word" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
        continue
      fi
      if $expect_after_runner; then
        case "$word" in
          run|exec|dlx|tool|-*) continue ;;
        esac
        expect_after_runner=false
      fi
      case "${word##*/}" in
        sudo|doas|env|command|time|nice|xargs|npx|pnpm|yarn|bunx|bun|pipx|uvx|uv|poetry|pipenv|rye|hatch|pdm|python|python2|python3|node|deno)
          expect_after_runner=true
          continue ;;
      esac
      past_cmd=true
      continue
    fi
    case "$word" in
      -*) continue ;;
    esac
    positionals+=("$word")
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  local i last_index=$(( ${#positionals[@]} - 1 ))
  for (( i = 0; i < last_index; i++ )); do
    printf '%s\n' "${positionals[$i]}"
  done
}

# Resolve a candidate source argument to its canonical absolute path, or
# print nothing (unresolved) on failure — the caller treats an unresolved
# source as fail-open, per this hook's documented "Known gaps" above. Skips
# candidates carrying unexpanded shell syntax ($VAR, $(...), backticks)
# rather than resolving them literally as filenames. readlink -f runs first
# (works on both BSD and GNU readlink); the cd+pwd -P fallback only covers
# readlink being entirely absent from PATH, not readlink -f failing on a
# particular candidate.
_relocation_resolve_source() {
  local cwd="$1" candidate="$2" resolved
  case "$candidate" in
    *'$'*|*'`'*) return 1 ;;
  esac
  if command -v readlink >/dev/null 2>&1; then
    resolved=$(cd -- "$cwd" 2>/dev/null && readlink -f -- "$candidate" 2>/dev/null) || return 1
  else
    resolved=$(cd -- "$cwd" 2>/dev/null && cd -- "$(dirname -- "$candidate")" 2>/dev/null \
      && printf '%s/%s' "$(pwd -P)" "$(basename -- "$candidate")") || return 1
  fi
  [ -n "$resolved" ] || return 1
  printf '%s' "$resolved"
}

FRAGMENTS=$(_lib_split_fragments "$COMMAND_UNQUOTED")
FRAGMENTS_SPLIT_EXIT=$?
if [ "$FRAGMENTS_SPLIT_EXIT" -ne 0 ]; then
  emit_deny "Blocked by repo-relocation hook: could not split the command into fragments (exit ${FRAGMENTS_SPLIT_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned relocation command."
  exit 0
fi
while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue

  is_relocation=false
  tool_label="mv"
  if _lib_fragment_invokes_tool "$fragment" mv; then
    is_relocation=true
  elif _lib_fragment_invokes_tool "$fragment" rsync && _lib_fragment_has_token "$fragment" --remove-source-files; then
    is_relocation=true
    tool_label="rsync --remove-source-files"
  fi
  $is_relocation || continue

  while IFS= read -r candidate; do
    [ -z "$candidate" ] && continue
    resolved=$(_relocation_resolve_source "$CWD" "$candidate")
    [ -z "$resolved" ] && continue

    if [ "$resolved" = "$REPO_ROOT" ] || [ "${REPO_ROOT#"$resolved"/}" != "$REPO_ROOT" ]; then
      emit_deny "Blocked by repo-relocation hook: this '$tool_label' command's source '$candidate' resolves to the claude-config checkout (or an ancestor of it) at '$resolved', and moving or renaming this checkout outside a supported path would break every stow symlink under ~/.claude/ and ~/.local/bin/ at once. Use 'relocate-claude-config <new-path>' instead — it unstows, moves, and re-stows this checkout safely, and also repairs an already-broken ~/.claude via 'relocate-claude-config --repair <new-path>'."
      exit 0
    fi
  done <<< "$(_relocation_positional_sources "$fragment")"
done <<< "$FRAGMENTS"

exit 0
