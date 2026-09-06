#!/bin/bash
# hook-class: gate
# Gate: deny Claude's Bash tool whenever the raw command text contains a credential-path token (SSH private key basename, .netrc/_netrc, .git-credentials, a cloud credential-store path, a non-template .env variant, credentials.json). Always on, no arming file, no bypass valve — closes the Bash-based read gap that deny-env-reads.sh and deny-data-file-reads.sh leave open by only gating the Read tool.
# Matches the path token alone, with no verb condition: the set of commands that can expose file content (vim, tee, dd, openssl, curl --upload-file, ...) is unbounded, so a verb allowlist would trade a bounded false-positive cost for an unbounded bypass surface. Also denies non-exposing commands like ssh-add/chmod/ssh -i — run those via the `!` shell escape instead.
# One exemption: a `.env`-shaped argument to a documented env-file loader flag (`--env-file`, `--env-file-if-exists`, `--envfile`) is stripped before the re-scan below, since that flag loads the file into a subprocess environment rather than printing it. See _lib_strip_env_file_flag_args in _lib.sh for the argument-shape and metacharacter-termination conditions that keep every other credential family denied in flag position.
#
# Documented residuals, each pinned by a regression test rather than solved:
# - The basename-token match (not path-qualified) also matches a bare string
#   search for an SSH private-key basename that never opens the file.
# - A command referencing a credential only through an earlier symlink/
#   rename under an innocuous name carries no credential-path token in the
#   text this hook sees.
# - The env-file exemption above still allows a runner to load then print
#   the file's own contents (`docker run --env-file=t/.env alpine env`) — a
#   deliberate print, not the accidental exposure this gate targets.
# - The exemption is inert argv padding to anything that doesn't parse the
#   flag, so `bash -c 'cat "$2"' _ --env-file <path>/.env` still reads the
#   file through the gate.
# - The strip's sed-failure fallback (BSD sed on an invalid UTF-8 byte) is
#   pinned by a unit test that's skipped whenever GNU sed is detected, so CI
#   (ubuntu-24.04, GNU sed) never exercises that regression test directly --
#   only a contributor's local BSD/macOS sed runs it.
# - COMMAND_UNQUOTED's sed/tr strip failure fails closed: its exit status is
#   checked immediately and denies rather than falling through to this
#   hook's normal allow path with the credential-path scan silently
#   unscanned.
#
# Fail-closed on unparseable hook input.

set -uo pipefail

DENY_GATE_LABEL="credential-path Bash"

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

_lib_parse_tool_input_or_deny "could not parse tool-input JSON. Refusing to evaluate the command under malformed input."

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Quote-stripped so an adjacent-quote split (e.g. `cat ~/.ssh/config"_backup"`,
# which bash executes identically to the unquoted form) can't slip the
# credential-path token past this scan -- see _lib_strip_shell_quotes.
# Checked and fail-closed, matching deny-invisible-commit-content.sh's own
# COMMAND_UNQUOTED computation -- an unchecked failure here would silently
# clear COMMAND_UNQUOTED and fall through to this hook's normal allow path.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
COMMAND_UNQUOTED_EXIT=$?
if [ "$COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned command with no bypass valve."
  exit 0
fi

# Case-folded (-i): on a case-insensitive-but-case-preserving filesystem (macOS APFS/HFS+, Windows NTFS), `id_RSA` opens the same file as `id_rsa` -- a case-sensitive match here would silently bypass a gate with no other bypass valve.
if printf '%s' "$COMMAND_UNQUOTED" | grep -qEi "$_LIB_CREDENTIAL_PATH_REGEX"; then
  # Strip only now that the raw text has already matched, then re-scan --
  # never the reverse order, which would hand an empty/no-op strip an
  # unmatched string and turn a strip failure into a silent allow. A cleared
  # re-scan falls through to the .ssh and credential-file-guard.md checks
  # below rather than returning allow outright, so a command that also
  # carries an unrelated credential-shaped token (one only those checks
  # catch) alongside an exempted env-file flag still reaches them.
  COMMAND_ENV_FILE_STRIPPED=$(_lib_strip_env_file_flag_args "$COMMAND_UNQUOTED")
  if printf '%s' "$COMMAND_ENV_FILE_STRIPPED" | grep -qEi "$_LIB_CREDENTIAL_PATH_REGEX"; then
    emit_deny "the command references a credential-shaped path (an SSH private key, .netrc/_netrc, .git-credentials, a cloud credential store, or a non-template .env/credentials.json path). Reading, copying, or otherwise touching a credential file through Bash pulls its content toward Claude's conversation context. No bypass valve — if this command is legitimate and does not expose file content (e.g. ssh-add, chmod, ssh -i), run it yourself via the ! shell escape instead of through Claude's Bash tool."
    exit 0
  fi
fi

# Custom-named SSH keys (deploy_key, github_actions_key, ...) have no fixed
# basename to enumerate above -- deny-by-default under .ssh instead.
if _lib_has_unsafe_ssh_dir_reference "$COMMAND_UNQUOTED"; then
  emit_deny "the command references a file under a .ssh-shaped directory that isn't on the safe-basename allowlist (authorized_keys, known_hosts, config, *.pub) -- likely a private key with a custom name. Reading, copying, or otherwise touching a credential file through Bash pulls its content toward Claude's conversation context. No bypass valve — if this command is legitimate and does not expose file content, run it yourself via the ! shell escape instead of through Claude's Bash tool."
  exit 0
fi

# --- Personal/org-specific path additions from credential-file-guard.md --
# Union, not swap: $(_lib_config_dir)'s copy wins if present, else the legacy $HOME/.claude location -- keeps an already-armed CLAUDE_CONFIG_DIR user's guard live.
# An unresolvable config dir leaves CREDENTIAL_FILE_GUARD at the legacy path; this is an opt-in guard, not a gate, so resolver failure must not disable it.
CREDENTIAL_FILE_GUARD="${HOME}/.claude/credential-file-guard.md"
if config_dir=$(_lib_config_dir) && [ -f "$config_dir/credential-file-guard.md" ]; then
  CREDENTIAL_FILE_GUARD="$config_dir/credential-file-guard.md"
fi
while IFS=$'\t' read -r _lineno line; do
  # shellcheck disable=SC2254 # $line is an intentional user-authored glob, mirroring deny-data-file-reads.sh's config-glob loop; quoting it would force literal matching and break every wildcard rule.
  # nocasematch, same rationale as the built-in regex above; bash `case` has no per-pattern case-fold syntax, so the shopt is scoped tightly around this one statement and restored immediately after.
  shopt -s nocasematch
  case "$COMMAND_UNQUOTED" in
    *$line*)
      emit_deny "the command matches the glob '${line}' in ~/.claude/credential-file-guard.md, a path shape you flagged as credential-bearing. (See docs/security-hardening.md.)"
      exit 0
      ;;
  esac
  shopt -u nocasematch
done < <(_lib_config_lines "$CREDENTIAL_FILE_GUARD")

exit 0
