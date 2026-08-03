#!/bin/bash
# hook-class: gate
# Gate: deny Bash commands that install a named software package or hand
# downloaded content to a shell/interpreter, so an agent cannot bring new
# software onto the machine on its own initiative. Always on, no arming
# file, no bypass valve.
#
# Matches on token PRESENCE (_lib_fragment_has_token), not on resolving
# "the" leading command: presence is robust to any wrapper (sudo, env
# VAR=1, timeout N, nohup) since none of those remove a token from the
# fragment string. Position-sensitive resolution (_lib_fragment_command_word
# and its wrapper _lib_fragment_invokes_tool) is not used here because their
# runner-skip list treats pnpm/yarn/bun as wrappers, resolving `pnpm add
# lodash` to the command word `add` rather than `pnpm` (see
# docs/security-hardening.md for the accepted residuals this trade produces).
#
# Named false-allows (accepted, not chased further):
#   - `pip install -e <VCS-URL>` — the editable-install marker's value is
#     always skipped, whether it's a local path or a fetchable URL.
#   - A path-prefixed manager invocation (`/opt/homebrew/bin/npm install x`)
#     — token-presence matching never sees `npm` inside the longer token.
#   - Bare `npx`/`bunx`/`uvx`/`pipx` with no `-y`/`--yes` — disambiguating
#     "runs an already-installed local tool" from "fetches a new one" needs
#     lockfile/package.json awareness this hook does not have.
#
# Named over-denies (accepted, not chased further):
#   - An unrecognized value-taking flag not in the restore-marker set
#     (`--registry <url>`, `--prefix <path>`) has its value misread as a
#     leftover token.
#   - A text argument merely containing manager+verb tokens (a grep pattern,
#     a commit message) denies uniformly regardless of quote placement —
#     see docs/security-hardening.md for the quote-stripping mechanism.
#     Workaround is the `!` shell escape.
#   - curl/wget co-occurring with a shell/interpreter anywhere in one Bash
#     call denies, regardless of which operator actually connects them
#     (`&&`, `;`, a pipe, or `bash -c "$(...)"`'s substitution form) — not
#     only a literal download-then-run pipeline.
#
# See docs/security-hardening.md for the full rationale and residuals list.
#
# Fail-closed on unparseable hook input.

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
  # _lib.sh's _lib_emit_deny comment).
  # shellcheck disable=SC2218
  emit_deny "Blocked by network-install gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by network-install gate: could not parse tool-input JSON. Refusing to evaluate the command under malformed input."

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Quote-stripped before matching so an adjacent-quote split (`"npm" install
# lodash` runs identically to `npm install lodash`) can't slip a manager or
# verb token past has-token's space/start-boundary check — same rationale
# and helper as deny-credential-bash-reads.sh's COMMAND_UNQUOTED.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")

_INSTALL_ALTERNATIVE="If this install is intentional, ask the user to run it themselves via the ! shell escape, which runs outside the tool-call path this hook gates."

# Restore-marker flags for the npm/pnpm/yarn/bun/pip/pip3/uv-pip family.
# Value-taking markers consume the token immediately following them;
# flag-only markers need no special handling beyond step 4's blanket
# "every token starting with -" removal.
_INSTALL_VALUE_TAKING_MARKERS="-r --requirement -e --editable"

# Scans $1 (a fragment already confirmed to have-token both a family's
# manager name(s) and its install verb). $2 is the verb token to drop (its
# first occurrence); the remaining args are manager tokens to drop (their
# first occurrence each — one word for npm/pnpm/yarn/bun/pip/pip3, two for
# the uv+pip family). Returns 0 (true) iff, after removing the manager
# token(s), the verb token, every VAR=value assignment, every pure-wrapper
# token (sudo/doas/env/command/time/nice/nohup/timeout, plus timeout's
# numeric duration argument), every flag (any token starting with -), and a
# value-taking restore marker's value token, at least one token survives —
# meaning this is a named-package install, not a bare restore.
_install_has_leftover_token() {
  local fragment="$1" verb="$2"
  shift 2
  local -a pending_managers=("$@")
  local saved_opts=$-
  set -f
  local word verb_pending=true
  local skip_next_value=false skip_next_if_numeric=false
  local leftover=false
  for word in $fragment; do
    if $skip_next_value; then
      skip_next_value=false
      continue
    fi
    if $skip_next_if_numeric; then
      skip_next_if_numeric=false
      case "$word" in
        ''|*[!0-9]*) ;;  # not purely numeric — falls through to normal handling below
        *) continue ;;
      esac
    fi
    local matched_manager=false i
    for i in "${!pending_managers[@]}"; do
      if [ "$word" = "${pending_managers[$i]}" ]; then
        unset -v 'pending_managers[i]'
        matched_manager=true
        break
      fi
    done
    if $matched_manager; then continue; fi
    if $verb_pending && [ "$word" = "$verb" ]; then
      verb_pending=false
      continue
    fi
    if [[ "$word" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      continue
    fi
    case "$word" in
      sudo|doas|env|command|time|nice|nohup) continue ;;
      timeout) skip_next_if_numeric=true; continue ;;
    esac
    case "$word" in
      -*)
        case " $_INSTALL_VALUE_TAKING_MARKERS " in
          *" $word "*) skip_next_value=true ;;
        esac
        continue
        ;;
    esac
    leftover=true
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  $leftover
}

# npm/pnpm/yarn/bun: install/i/add verb, single manager token.
_install_check_npm_family() {
  local fragment="$1" manager verb
  for manager in npm pnpm yarn bun; do
    _lib_fragment_has_token "$fragment" "$manager" || continue
    for verb in install i add; do
      _lib_fragment_has_token "$fragment" "$verb" || continue
      if _install_has_leftover_token "$fragment" "$verb" "$manager"; then
        return 0
      fi
    done
  done
  return 1
}

# pip/pip3/uv-pip: install verb only, one or two manager tokens. Manager-set
# selection is mutually exclusive (uv present -> the uv+pip pair, else pip3,
# else pip alone) rather than trying each variant independently — trying
# bare "pip" against a fragment that also has-tokens "uv" previously denied
# `uv pip install -r requirements.txt` (a legitimate restore), because "uv"
# read as an unrecognized leftover token under a removal set that never
# included it.
_install_check_pip_family() {
  local fragment="$1"
  local -a mgr_words
  if _lib_fragment_has_token "$fragment" uv; then
    _lib_fragment_has_token "$fragment" pip || return 1
    mgr_words=(uv pip)
  elif _lib_fragment_has_token "$fragment" pip3; then
    mgr_words=(pip3)
  elif _lib_fragment_has_token "$fragment" pip; then
    mgr_words=(pip)
  else
    return 1
  fi
  _lib_fragment_has_token "$fragment" install || return 1
  _install_has_leftover_token "$fragment" install "${mgr_words[@]}"
}

# npx/bunx/uvx with an explicit -y/--yes (the unambiguous skip-confirmation-
# and-fetch signal), and `pipx run` with the same flag.
_install_check_npx_family() {
  local fragment="$1" tool
  local has_yes=false
  if _lib_fragment_has_token "$fragment" -y || _lib_fragment_has_token "$fragment" --yes; then
    has_yes=true
  fi
  $has_yes || return 1
  for tool in npx bunx uvx; do
    _lib_fragment_has_token "$fragment" "$tool" && return 0
  done
  if _lib_fragment_has_token "$fragment" pipx && _lib_fragment_has_token "$fragment" run; then
    return 0
  fi
  return 1
}

SAW_DOWNLOADER_FRAGMENT=""
SAW_INTERPRETER_FRAGMENT=""

while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue

  if _install_check_npm_family "$fragment"; then
    emit_deny "Blocked by network-install gate: this command installs a named package via npm/pnpm/yarn/bun (adds software from a registry rather than restoring already-declared dependencies). $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  if _install_check_pip_family "$fragment"; then
    emit_deny "Blocked by network-install gate: this command installs a named package via pip/pip3/uv pip (adds software from a registry rather than restoring already-declared dependencies). $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  if _install_check_npx_family "$fragment"; then
    emit_deny "Blocked by network-install gate: this command uses npx/bunx/uvx/pipx's explicit -y/--yes flag to skip confirmation and fetch-and-run a package — the same shape as a named install. $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  for tool in curl wget; do
    if _lib_fragment_has_token "$fragment" "$tool"; then
      SAW_DOWNLOADER_FRAGMENT="$fragment"
    fi
  done
  for tool in bash sh zsh python3 node ruby perl; do
    if _lib_fragment_has_token "$fragment" "$tool"; then
      SAW_INTERPRETER_FRAGMENT="$fragment"
    fi
  done
done <<< "$(_lib_split_fragments "$COMMAND_UNQUOTED")"

if [ -n "$SAW_DOWNLOADER_FRAGMENT" ] && [ -n "$SAW_INTERPRETER_FRAGMENT" ]; then
  emit_deny "Blocked by network-install gate: this command both fetches content (curl/wget) and hands it to a shell or interpreter (bash/sh/zsh/python3/node/ruby/perl) in the same call — the download-and-execute pattern this hook blocks regardless of which operator connects the two, including curl/wget and the interpreter appearing in unrelated parts of a batched command. $_INSTALL_ALTERNATIVE"
  exit 0
fi

# Process substitution (`bash <(curl ...)`) is not decomposed by
# _lib_split_fragments, so it needs a direct substring check against the
# raw command text rather than the fragment loop above. Best-effort
# heuristic, not a parser — matches how _LIB_CREDENTIAL_PATH_REGEX is
# itself substring-based rather than a full shell grammar.
case "$COMMAND_UNQUOTED" in
  *'<(curl'*|*'<(wget'*)
    emit_deny "Blocked by network-install gate: this command feeds a curl/wget process substitution directly to a shell — the same download-and-execute pattern as a piped installer. $_INSTALL_ALTERNATIVE"
    exit 0
    ;;
esac

exit 0
