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

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by env-read gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked: could not parse tool-input JSON for env-read gate."

# Defense-in-depth: only act on Read calls (settings.json already matches Read).
if [ "$TOOL_NAME" != "Read" ]; then
  exit 0
fi

FILE_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -z "$FILE_PATH" ] && exit 0

BASENAME=$(basename -- "$FILE_PATH")

case "$BASENAME" in
  .env.example|.env.template|.env.sample)
    : ;;  # allowlist candidate — fall through to symlink-target check
  .env|.env.*)
    emit_deny "Read of '${FILE_PATH}' denied by env-read gate. Dotenv files commonly hold secrets; reading pulls them into Claude's conversation context. If this is a non-secret template, rename it to .env.example, .env.template, or .env.sample. Otherwise inspect it with a shell command (e.g. \`! cat ${FILE_PATH}\`) instead of the Read tool. (Allowlist: ~/.claude/hooks/deny-env-reads.sh)"
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
    emit_deny "Read of '${FILE_PATH}' denied: symlink target is unresolvable or missing. Fail-closed — the env-read gate cannot verify what file would actually be read."
    exit 0
  fi
  RESOLVED_BASENAME=$(basename -- "$RESOLVED")
  case "$RESOLVED_BASENAME" in
    .env.example|.env.template|.env.sample)
      exit 0 ;;
    .env|.env.*)
      emit_deny "Read of '${FILE_PATH}' denied: the symlink resolves to '${RESOLVED}', whose basename matches a denied env pattern."
      exit 0
      ;;
    *)
      exit 0 ;;
  esac
fi

exit 0
