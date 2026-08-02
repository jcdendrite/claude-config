#!/bin/bash
# hook-class: gate
# Gate: deny Claude's Read tool on credential-shaped file paths (SSH private keys, .netrc/_netrc, .git-credentials, cloud credential stores, non-template .env variants, credentials.json). Always on, no arming file, no bypass valve -- unlike deny-env-reads.sh's allowlist-and-symlink-defense design (built for .env.example-style safe templates), none of these path shapes has a legitimate secret-free variant.
# Resolves symlinks via readlink -f before allowing, same fail-closed-on-unresolvable posture as deny-env-reads.sh; since this hook carries no allowlist, every symlinked Read target is resolved and checked, not just ones whose own name looks credential-shaped. Requires GNU coreutils (greadlink on macOS pre-12.3).
#
# Scope: Read tool only; deny-credential-bash-reads.sh covers Bash.
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
  emit_deny "Blocked by credential-file read gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by credential-file read gate: could not parse tool-input JSON. Refusing to evaluate the Read under malformed input."

# Defense-in-depth: only act on Read calls (settings.json already matches Read).
if [ "$TOOL_NAME" != "Read" ]; then
  exit 0
fi

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FILE_PATH" ] && exit 0

# Optional personal/org additions: one glob per line (same grammar as data-file-read-guard.md). See docs/security-hardening.md.
CREDENTIAL_FILE_GUARD="${HOME}/.claude/credential-file-guard.md"

# Tests $1 (a raw or symlink-resolved path) against the built-in regex, then the optional user glob file. Shared by both call sites so pre- and post-symlink-resolution checks aren't written out twice.
_matches_credential_path() {
  local path="$1"
  # Case-folded (-i): on a case-insensitive-but-case-preserving filesystem (macOS APFS/HFS+, Windows NTFS), `id_RSA` opens the same file as `id_rsa` -- a case-sensitive match here would silently bypass a gate with no other bypass valve.
  if printf '%s' "$path" | grep -qEi "$_LIB_CREDENTIAL_PATH_REGEX"; then
    return 0
  fi
  [ -f "$CREDENTIAL_FILE_GUARD" ] && [ -r "$CREDENTIAL_FILE_GUARD" ] || return 1
  local raw_line line
  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    # Strip CR (CRLF), then leading/trailing whitespace.
    line=${raw_line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    case "$line" in '#'*) continue ;; esac

    # nocasematch, same rationale as the built-in regex above; bash `case` has no per-pattern case-fold syntax. Scoped tightly and restored immediately after -- this function is called again for the symlink-resolved path, so the shopt must not leak past this one match attempt.
    shopt -s nocasematch
    # shellcheck disable=SC2254 # $line is an intentional user-authored glob; see deny-data-file-reads.sh's identical config-glob loop for rationale.
    case "$path" in
      $line)
        shopt -u nocasematch
        return 0
        ;;
    esac
    shopt -u nocasematch
  done < "$CREDENTIAL_FILE_GUARD"
  return 1
}

if _matches_credential_path "$FILE_PATH"; then
  emit_deny "Read of '${FILE_PATH}' denied by the credential-file read gate: the path is credential-shaped (an SSH private key, .netrc/_netrc, .git-credentials, a cloud credential store, a non-template .env variant, credentials.json, or a path flagged in ~/.claude/credential-file-guard.md). Reading it pulls a live secret into Claude's conversation context. No bypass valve — inspect it with a shell command (e.g. \`! cat ${FILE_PATH}\`) instead of the Read tool."
  exit 0
fi

# Resolve symlinks before allowing — an innocuously-named file that symlinks
# to a credential path would otherwise pass the raw-path check above.
if [ -L "$FILE_PATH" ]; then
  RESOLVED=$(readlink -f -- "$FILE_PATH" 2>/dev/null)
  if [ -z "$RESOLVED" ] || [ ! -e "$RESOLVED" ]; then
    emit_deny "Read of '${FILE_PATH}' denied by the credential-file read gate: symlink target is unresolvable or missing. Fail-closed — the gate cannot verify what file would actually be read."
    exit 0
  fi
  if _matches_credential_path "$RESOLVED"; then
    emit_deny "Read of '${FILE_PATH}' denied by the credential-file read gate: the symlink resolves to '${RESOLVED}', a credential-shaped path."
    exit 0
  fi
fi

exit 0
