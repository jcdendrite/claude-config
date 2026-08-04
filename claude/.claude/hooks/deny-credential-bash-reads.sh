#!/bin/bash
# hook-class: gate
# Gate: deny Claude's Bash tool whenever the raw command text contains a credential-path token (SSH private key basename, .netrc/_netrc, .git-credentials, a cloud credential-store path, a non-template .env variant, credentials.json). Always on, no arming file, no bypass valve — closes the Bash-based read gap that deny-env-reads.sh and deny-data-file-reads.sh leave open by only gating the Read tool.
# Matches the path token alone, with no verb condition: the set of commands that can expose file content (vim, tee, dd, openssl, curl --upload-file, ...) is unbounded, so a verb allowlist would trade a bounded false-positive cost for an unbounded bypass surface. Also denies non-exposing commands like ssh-add/chmod/ssh -i — run those via the `!` shell escape instead.
# Documented residuals, both pinned by regression tests rather than solved: the basename-token match (not path-qualified) also matches a bare string search like `grep id_rsa .` that never opens the file; and a command referencing a credential only through an earlier symlink/rename under an innocuous name carries no credential-path token in the text this hook sees.
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
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by credential-path Bash gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by credential-path Bash gate: could not parse tool-input JSON. Refusing to evaluate the command under malformed input."

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Quote-stripped so an adjacent-quote split (e.g. `cat ~/.ssh/config"_backup"`,
# which bash executes identically to the unquoted form) can't slip the
# credential-path token past this scan -- see _lib_strip_shell_quotes.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")

# Case-folded (-i): on a case-insensitive-but-case-preserving filesystem (macOS APFS/HFS+, Windows NTFS), `id_RSA` opens the same file as `id_rsa` -- a case-sensitive match here would silently bypass a gate with no other bypass valve.
if printf '%s' "$COMMAND_UNQUOTED" | grep -qEi "$_LIB_CREDENTIAL_PATH_REGEX"; then
  emit_deny "Blocked by credential-path Bash gate: the command references a credential-shaped path (an SSH private key, .netrc/_netrc, .git-credentials, a cloud credential store, or a non-template .env/credentials.json path). Reading, copying, or otherwise touching a credential file through Bash pulls its content toward Claude's conversation context. No bypass valve — if this command is legitimate and does not expose file content (e.g. ssh-add, chmod, ssh -i), run it yourself via the ! shell escape instead of through Claude's Bash tool."
  exit 0
fi

# Custom-named SSH keys (deploy_key, github_actions_key, ...) have no fixed
# basename to enumerate above -- deny-by-default under .ssh instead.
if _lib_has_unsafe_ssh_dir_reference "$COMMAND_UNQUOTED"; then
  emit_deny "Blocked by credential-path Bash gate: the command references a file under a .ssh-shaped directory that isn't on the safe-basename allowlist (authorized_keys, known_hosts, config, *.pub) -- likely a private key with a custom name. Reading, copying, or otherwise touching a credential file through Bash pulls its content toward Claude's conversation context. No bypass valve — if this command is legitimate and does not expose file content, run it yourself via the ! shell escape instead of through Claude's Bash tool."
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
      emit_deny "Blocked by credential-path Bash gate: the command matches the glob '${line}' in ~/.claude/credential-file-guard.md, a path shape you flagged as credential-bearing. (See docs/security-hardening.md.)"
      exit 0
      ;;
  esac
  shopt -u nocasematch
done < <(_lib_config_lines "$CREDENTIAL_FILE_GUARD")

exit 0
