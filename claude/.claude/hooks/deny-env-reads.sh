#!/bin/bash
# hook-class: gate
# Gate: deny Claude's Read tool on .env* files that commonly hold secrets.
# Allows the three conventional non-secret template suffixes:
#   .env.example  .env.template  .env.sample
# Denies .env and any .env.* not on that allowlist.
#
# Symlink defense: after an allowlist basename match, the hook resolves the
# path with readlink -f and re-checks the target's basename. This defeats the
# .env.example -> .env.production bypass. Broken-symlink or unresolvable
# target fails closed. readlink -f requires GNU coreutils (standard on Linux;
# on macOS pre-12.3 use greadlink via `brew install coreutils` if needed).
# Without it, symlinked templates fail closed — safe but conservative.
#
# Known limitation: hard links are NOT defended. Two directory entries sharing
# one inode would require lstat + inode comparison, which is overkill for the
# threat model here (prompt-injection or accidental access, not a privileged
# on-system attacker). Documented, not closed.
#
# Deliberate divergence from deny-private-project-refs.sh's "generic message"
# invariant: the deny reason echoes FILE_PATH. Env paths are not user-flagged-
# sensitive identifiers — naming the path helps the user see which Read was
# blocked without leaking anything private.
#
# Scope: Read tool only. Bash(cat .env.*) is out of scope by design — CLAUDE.md
# directs Claude to the ! shell-escape valve for non-Read inspection, which
# depends on Bash being unrestricted for these paths.

set -uo pipefail

DENY_GATE_LABEL="env-read"

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

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

# Defense-in-depth: only act on Read calls (settings.json already matches Read).
if [ "$TOOL_NAME" != "Read" ]; then
  exit 0
fi

[ -z "$FILE_PATH" ] && exit 0

BASENAME=$(basename -- "$FILE_PATH")

case "$BASENAME" in
  .env.example|.env.template|.env.sample)
    : ;;  # allowlist candidate — fall through to symlink-target check
  .env|.env.*)
    emit_deny "Read of '${FILE_PATH}' — Dotenv files commonly hold secrets; reading pulls them into Claude's conversation context. If this is a non-secret template, rename it to .env.example, .env.template, or .env.sample. Otherwise inspect it with a shell command (e.g. \`! cat ${FILE_PATH}\`) instead of the Read tool. (Allowlist: ~/.claude/hooks/deny-env-reads.sh)"
    exit 0
    ;;
  *)
    exit 0 ;;
esac

# Allowlist matched. Resolve symlinks before allowing — a .env.example that
# symlinks to .env.production would pass the basename check and expose secrets.
if [ -L "$FILE_PATH" ]; then
  RESOLVED=$(readlink -f -- "$FILE_PATH" 2>/dev/null)
  if [ -z "$RESOLVED" ] || [ ! -e "$RESOLVED" ]; then
    emit_deny "Read of '${FILE_PATH}' — symlink target is unresolvable or missing. Fail-closed — the env-read gate cannot verify what file would actually be read."
    exit 0
  fi
  RESOLVED_BASENAME=$(basename -- "$RESOLVED")
  case "$RESOLVED_BASENAME" in
    .env.example|.env.template|.env.sample)
      exit 0 ;;
    .env|.env.*)
      emit_deny "Read of '${FILE_PATH}' — the symlink resolves to '${RESOLVED}', whose basename matches a denied env pattern."
      exit 0
      ;;
    *)
      exit 0 ;;
  esac
fi

exit 0
