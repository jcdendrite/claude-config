#!/bin/bash
# hook-class: gate
# Gate: deny Bash commands that install a named package or hand downloaded
# content to a shell/interpreter. Always on, no arming file, no bypass valve.
# Matches on token presence, not on resolving the leading command through
# wrappers, since position-based resolution has gaps this trade avoids.
#
# Known gaps (accepted, not chased further — rationale: docs/security-hardening.md):
#   - `pip install -e <VCS-URL>` allows: the editable-install marker's value
#     is always skipped.
#   - Bare `npx`/`bunx`/`uvx`/`pipx` (no `-y`/`--yes`) allows: telling an
#     already-installed local tool from a fresh fetch needs lockfile
#     awareness this hook doesn't have.
#   - An unrecognized value-taking flag (e.g. `--registry <url>`) denies a
#     legitimate restore: its value is misread as a leftover token.
#   - A text argument merely mentioning manager+verb tokens (a grep pattern,
#     a commit message) denies uniformly, regardless of quote placement.
#   - curl/wget co-occurring with a shell/interpreter anywhere in one call
#     denies, regardless of which operator actually connects them.
#   - A `timeout` flag preceding its duration (e.g. `timeout --foreground
#     30s npm install`) denies a legitimate restore: the numeric-skip guard
#     only inspects the single token immediately after `timeout`.
#   - A heredoc/here-string redirect (`<<`/`<<<`) glued to a bare install
#     denies: its target is a multi-line delimited body, not a simple next
#     word, and none of the redirection branches below recognize it.
#   - A quoted argument that becomes redirect-shaped after this hook's own
#     quote-stripping (e.g. `npm install ">pkg"` becomes the bare word
#     `>pkg`) allows: neither npm's nor PyPI's package-name grammar permits
#     a literal `>`/`<` character, so nothing this shape can resolve to a
#     real package.
#   - `_lib_split_fragments` splits on any literal `|`, including the one
#     inside bash's `>|` (noclobber-override) redirect operator, so a
#     redirect placed before a trailing package-name argument (e.g. `npm
#     install >|/tmp/x evil-pkg`) evades this hook — accepted, since
#     closing it means changing shared `_lib_split_fragments`, used by
#     every hook in this suite (see docs/security-hardening.md).
#   - A manager binary whose own filename contains a space, invoked quoted
#     (e.g. `"/tmp/n pm" install x`), allows: `_lib_strip_shell_quotes`
#     removes the quotes before word-splitting, so the quoted single token
#     becomes two unquoted words that neither matches the manager name —
#     pre-existing under exact-token matching too, accepted since fixing
#     it means quote-position tracking through shared
#     `_lib_strip_shell_quotes`, used by every hook in this suite.
#   - Path-prefixed interpreter/downloader *references* (not invocations)
#     also co-occurrence-deny (e.g. `curl ... && ls ~/.nvm/.../bin/node`),
#     since the path-prefix matcher matches any word ending in `/node`
#     including a mere `ls`/`chmod` argument — the same accepted over-deny
#     direction as the operator-adjacency bullet above, extended to
#     reference-only mentions.
#   - COMMAND_UNQUOTED's sed/tr strip failure and the fragment split's own
#     sed failure both fail closed: each exit status is checked immediately
#     and denies rather than falling through to this hook's normal allow
#     path with the install/curl-pipe-bash scan silently unscanned.
#
# Fail-closed on unparseable hook input.

set -uo pipefail

# Bootstrap so a failed source of _lib.sh can still deny; re-pointed at
# _lib_emit_deny once sourced — see _lib.sh for the full contract.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # shellcheck disable=SC2218 # false positive: this stub-then-override redefinition resolves correctly at call time.
  emit_deny "Blocked by network-install gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by network-install gate: could not parse tool-input JSON. Refusing to evaluate the command under malformed input."

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Quote-stripped so an adjacent-quote split (`"npm" install x`) can't dodge
# has-token's boundary check — same helper as deny-credential-bash-reads.sh.
# Checked and fail-closed, matching deny-invisible-commit-content.sh's own
# COMMAND_UNQUOTED computation -- an unchecked failure here would silently
# clear COMMAND_UNQUOTED and fall through to this hook's normal allow path.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
COMMAND_UNQUOTED_EXIT=$?
if [ "$COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "Blocked by network-install gate: could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned command with no bypass valve."
  exit 0
fi

_INSTALL_ALTERNATIVE="If this install is intentional, name the package, its exact version constraint, and why, then ask the user to run it themselves via the ! shell escape, which runs outside the tool-call path this hook gates."

_INSTALL_VALUE_TAKING_MARKERS="-r --requirement -e --editable"

# True iff WORD equals NAME or ends in "/NAME" (a path-prefixed invocation,
# e.g. /opt/homebrew/bin/npm). Mirrors _lib_fragment_invokes_git's word test
# (_lib.sh:440), parameterized on NAME instead of hardcoded to git. Local to
# this hook rather than promoted to _lib.sh's _lib_fragment_has_token: that
# helper is shared with deny-reviewer-tree-mutation.sh and
# deny-repo-relocation.sh for flag matching (--fix, --remove-source-files),
# and widening it there would loosen unrelated flag checks in two untouched
# hooks.
_install_word_matches_name() {
  local word="$1" name="$2"
  [[ "$word" == "$name" || "$word" == */"$name" ]]
}

# True iff FRAGMENT contains a word matching NAME per _install_word_matches_name.
# On match, sets $_INSTALL_MATCHED_WORD to the matched word so callers can
# name it in a deny message — a side-effect global rather than a $(...)
# stdout capture, since this runs once per manager candidate per fragment
# and a subshell fork per call was a measured latency regression on a
# many-fragment command. Carries the same set -f/set +f guard as
# _install_has_leftover_token: the word loop is unquoted, so an unguarded
# scan picks up glob expansion on a crafted */? argument.
_install_fragment_manager_word() {
  local fragment="$1" name="$2"
  local saved_opts=$-
  set -f
  local word
  _INSTALL_MATCHED_WORD=""
  for word in $fragment; do
    if _install_word_matches_name "$word" "$name"; then
      _INSTALL_MATCHED_WORD="$word"
      break
    fi
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  [ -n "$_INSTALL_MATCHED_WORD" ]
}

# _install_has_leftover_token FRAGMENT VERB MANAGER... — true iff FRAGMENT,
# minus VERB, each MANAGER, every flag, and a value-taking marker's value,
# still has a token left (a named package, not a bare restore). See
# docs/security-hardening.md for the rule.
_install_has_leftover_token() {
  local fragment="$1" verb="$2"
  shift 2
  local -a pending_managers=("$@")
  local saved_opts=$-
  set -f
  local word verb_pending=true
  local skip_next_value=false skip_next_if_numeric=false
  local leftover=false
  # _lib_split_fragments doesn't split on `>`/`<`, so a bare install with
  # glued redirection (`2>&1`, `> out.log`) reads as a leftover token unless
  # recognized here; all three regexes are start-anchored so a real
  # leftover token glued to an operator (`evil-package>out.log`) still denies.
  local redirect_dup_re='^[0-9]*(>&[0-9-]+|<&[0-9-]+)$'
  # No `>|` alternative: `_lib_split_fragments` splits on literal `|` before
  # any fragment reaches this function, so a word containing an intact `>|`
  # never survives to be matched here (see the header's "Known gaps").
  local redirect_op_re='^([0-9]*(>>|<>|>|<)|&>>|&>)$'
  # A bare `>`/`<` glued target must not start with `&`: that shape belongs
  # to redirect_dup_re (`2>&1`), and when it doesn't fully match there the
  # `&`-prefixed remainder is not a valid target either -- real bash rejects
  # it as an ambiguous redirect, so it must fall through to leftover=true
  # rather than being swallowed as a glued filename here.
  local redirect_glued_re='^([0-9]*(>>|<>)|&>>|&>)[^[:space:]]+$|^[0-9]*(>|<)[^[:space:]&][^[:space:]]*$'
  for word in $fragment; do
    if $skip_next_value; then
      skip_next_value=false
      continue
    fi
    if $skip_next_if_numeric; then
      skip_next_if_numeric=false
      # GNU timeout's DURATION grammar: a number with an optional single
      # s/m/h/d suffix (info coreutils 'timeout invocation').
      if [[ "$word" =~ ^[0-9]+(\.[0-9]+)?[smhd]?$ ]]; then
        continue
      fi
    fi
    local matched_manager=false i
    for i in "${!pending_managers[@]}"; do
      if _install_word_matches_name "$word" "${pending_managers[$i]}"; then
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
    if [[ "$word" =~ $redirect_dup_re ]]; then
      continue
    fi
    if [[ "$word" =~ $redirect_op_re ]]; then
      skip_next_value=true
      continue
    fi
    if [[ "$word" =~ $redirect_glued_re ]]; then
      continue
    fi
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
  local fragment="$1" manager verb matched_token
  for manager in npm pnpm yarn bun; do
    _install_fragment_manager_word "$fragment" "$manager" || continue
    matched_token="$_INSTALL_MATCHED_WORD"
    for verb in install i add; do
      _lib_fragment_has_token "$fragment" "$verb" || continue
      if _install_has_leftover_token "$fragment" "$verb" "$manager"; then
        NETWORK_INSTALL_MATCHED_TOKEN="$matched_token"
        return 0
      fi
    done
  done
  return 1
}

# Manager-set selection is mutually exclusive (uv -> the uv+pip pair, else
# pip3, else pip alone): trying bare "pip" against a fragment that also has
# "uv" reads "uv" as a leftover token and false-denies a legitimate restore.
# Known seam: the `uv` branch checks for token `pip`, not `pip3`, so
# `uv pip3 install <pkg>` returns 1 (allow) from inside that branch without
# ever reaching the `pip3`/`pip`-alone branches below — not realistically
# reachable, since `uv pip3` isn't valid `uv` CLI syntax.
_install_check_pip_family() {
  local fragment="$1"
  local -a mgr_words
  local matched_token
  if _install_fragment_manager_word "$fragment" uv; then
    matched_token="$_INSTALL_MATCHED_WORD"
    _install_fragment_manager_word "$fragment" pip || return 1
    mgr_words=(uv pip)
  elif _install_fragment_manager_word "$fragment" pip3; then
    matched_token="$_INSTALL_MATCHED_WORD"
    mgr_words=(pip3)
  elif _install_fragment_manager_word "$fragment" pip; then
    matched_token="$_INSTALL_MATCHED_WORD"
    mgr_words=(pip)
  else
    return 1
  fi
  _lib_fragment_has_token "$fragment" install || return 1
  if _install_has_leftover_token "$fragment" install "${mgr_words[@]}"; then
    NETWORK_INSTALL_MATCHED_TOKEN="$matched_token"
    return 0
  fi
  return 1
}

# npx/bunx/uvx with an explicit -y/--yes (the unambiguous skip-confirmation-
# and-fetch signal), and `pipx run`/`npm exec` with the same flag.
_install_check_npx_family() {
  local fragment="$1" tool matched_token
  local has_yes=false
  if _lib_fragment_has_token "$fragment" -y || _lib_fragment_has_token "$fragment" --yes; then
    has_yes=true
  fi
  $has_yes || return 1
  for tool in npx bunx uvx; do
    _install_fragment_manager_word "$fragment" "$tool" || continue
    NETWORK_INSTALL_MATCHED_TOKEN="$_INSTALL_MATCHED_WORD"
    return 0
  done
  if _install_fragment_manager_word "$fragment" pipx && _lib_fragment_has_token "$fragment" run; then
    NETWORK_INSTALL_MATCHED_TOKEN="$_INSTALL_MATCHED_WORD"
    return 0
  fi
  if _install_fragment_manager_word "$fragment" npm && _lib_fragment_has_token "$fragment" exec; then
    NETWORK_INSTALL_MATCHED_TOKEN="$_INSTALL_MATCHED_WORD"
    return 0
  fi
  return 1
}

# uv add fetches a named package from PyPI, the same shape as `pip install
# <pkg>` — distinct verb from the uv-pip family above, so it needs its own
# leftover-token check rather than extending _install_check_pip_family.
_install_check_uv_add() {
  local fragment="$1" matched_token
  _install_fragment_manager_word "$fragment" uv || return 1
  matched_token="$_INSTALL_MATCHED_WORD"
  _lib_fragment_has_token "$fragment" add || return 1
  if _install_has_leftover_token "$fragment" add uv; then
    NETWORK_INSTALL_MATCHED_TOKEN="$matched_token"
    return 0
  fi
  return 1
}

# pnpm/yarn dlx always fetch and run a package in a throwaway environment —
# unlike npx, neither ever resolves to an already-installed local binary, so
# this denies unconditionally rather than requiring -y/--yes.
_install_check_dlx_family() {
  local fragment="$1" manager matched_token
  for manager in pnpm yarn; do
    _install_fragment_manager_word "$fragment" "$manager" || continue
    matched_token="$_INSTALL_MATCHED_WORD"
    if _lib_fragment_has_token "$fragment" dlx; then
      NETWORK_INSTALL_MATCHED_TOKEN="$matched_token"
      return 0
    fi
  done
  return 1
}

SAW_DOWNLOADER_FRAGMENT=""
SAW_INTERPRETER_FRAGMENT=""
NETWORK_INSTALL_MATCHED_TOKEN=""

FRAGMENTS=$(_lib_split_fragments "$COMMAND_UNQUOTED")
FRAGMENTS_SPLIT_EXIT=$?
if [ "$FRAGMENTS_SPLIT_EXIT" -ne 0 ]; then
  emit_deny "Blocked by network-install gate: could not split the command into fragments (exit ${FRAGMENTS_SPLIT_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned command with no bypass valve."
  exit 0
fi
while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue

  if _install_check_npm_family "$fragment"; then
    emit_deny "Blocked by network-install gate: this command installs a named package via npm/pnpm/yarn/bun (matched manager token '$NETWORK_INSTALL_MATCHED_TOKEN'; adds software from a registry rather than restoring already-declared dependencies). $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  if _install_check_pip_family "$fragment"; then
    emit_deny "Blocked by network-install gate: this command installs a named package via pip/pip3/uv pip (matched manager token '$NETWORK_INSTALL_MATCHED_TOKEN'; adds software from a registry rather than restoring already-declared dependencies). $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  if _install_check_npx_family "$fragment"; then
    emit_deny "Blocked by network-install gate: this command uses npx/bunx/uvx/pipx/npm-exec's explicit -y/--yes flag to skip confirmation and fetch-and-run a package (matched manager token '$NETWORK_INSTALL_MATCHED_TOKEN') — the same shape as a named install. $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  if _install_check_uv_add "$fragment"; then
    emit_deny "Blocked by network-install gate: this command installs a named package via uv add (matched manager token '$NETWORK_INSTALL_MATCHED_TOKEN'; adds software from a registry rather than restoring already-declared dependencies). $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  if _install_check_dlx_family "$fragment"; then
    emit_deny "Blocked by network-install gate: this command uses pnpm/yarn dlx (matched manager token '$NETWORK_INSTALL_MATCHED_TOKEN') to fetch and run a package in a throwaway environment — the same shape as a named install, with no local-resolution ambiguity to disambiguate. $_INSTALL_ALTERNATIVE"
    exit 0
  fi

  for tool in curl wget; do
    if _install_fragment_manager_word "$fragment" "$tool"; then
      SAW_DOWNLOADER_FRAGMENT="$fragment"
    fi
  done
  for tool in bash sh zsh python3 python node ruby perl; do
    if _install_fragment_manager_word "$fragment" "$tool"; then
      SAW_INTERPRETER_FRAGMENT="$fragment"
    fi
  done
done <<< "$FRAGMENTS"

if [ -n "$SAW_DOWNLOADER_FRAGMENT" ] && [ -n "$SAW_INTERPRETER_FRAGMENT" ]; then
  emit_deny "Blocked by network-install gate: this command both fetches content (curl/wget) and hands it to a shell or interpreter (bash/sh/zsh/python3/node/ruby/perl) in the same call — the download-and-execute pattern this hook blocks regardless of which operator connects the two, including curl/wget and the interpreter appearing in unrelated parts of a batched command. $_INSTALL_ALTERNATIVE"
  exit 0
fi

# Process substitution isn't decomposed by _lib_split_fragments, so it needs
# a direct substring check — a heuristic, not a parser.
case "$COMMAND_UNQUOTED" in
  *'<(curl'*|*'<(wget'*)
    emit_deny "Blocked by network-install gate: this command feeds a curl/wget process substitution directly to a shell — the same download-and-execute pattern as a piped installer. $_INSTALL_ALTERNATIVE"
    exit 0
    ;;
esac

exit 0
