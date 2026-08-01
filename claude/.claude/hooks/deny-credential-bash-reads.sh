#!/bin/bash
# hook-class: gate
# Gate: deny Claude's Bash tool whenever the raw command text contains a
# credential-path token — an SSH private key basename, .netrc/_netrc,
# .git-credentials, a cloud credential-store path, or (redundantly with the
# native permissions.deny rules, at a different enforcement layer) a
# non-template .env variant or credentials.json. Always on, no arming file.
#
# Closes the gap the Read-only guards (deny-env-reads.sh,
# deny-data-file-reads.sh) both document explicitly in their own header
# comments: Bash-based reads of a credential file were never gated before
# this hook existed.
#
# No content-exposing-verb carve-out. A design requiring a verb like
# `cat`/`head`/`grep` alongside the path token — so `ssh-add ~/.ssh/id_rsa`,
# `chmod 600 ~/.ssh/id_rsa`, and `ssh -i ~/.ssh/id_rsa host` would pass —
# was considered and rejected: the set of commands that can expose file
# content via some verb is unbounded (`vim`, `tee`, `dd`, `openssl`, `cp ...
# /dev/stdout`, `curl --upload-file`, and countless one-liners), so a verb
# allowlist would trade a small, bounded false-positive cost for an
# unbounded false-negative bypass surface. This hook matches on the path
# token alone, with no verb condition and no bypass valve. The accepted
# false-positive cost — denying `ssh-add`/`chmod`/`ssh -i` too — is the same
# trade-off deny-data-file-reads.sh already accepts for legitimately-named
# non-PHI files sharing a flagged extension: inspect or run the specific
# command via the `!` shell escape instead.
#
# Path-token matching is basename-based, not path-qualified, and this is a
# deliberate trade-off: _LIB_CREDENTIAL_PATH_REGEX matches a bare token like
# `id_rsa` anywhere in the command text, not only when directory-qualified.
# This closes a `cd ~/.ssh && cat id_rsa` bypass (the bare token still
# matches) at the cost of a documented residual: `grep "id_rsa" .`
# (searching for that literal string, not opening a file by that name) also
# matches, since the hook has no shell semantics to distinguish a filename
# argument from a search-pattern argument. Accepted and pinned by a
# regression test rather than solved — a tripwire, not an airtight gate.
#
# Documented residual — symlink/rename bypass. A command that references a
# credential path only through an earlier symlink or rename under an
# innocuous name (e.g. `cat notes.txt`, once `notes.txt` was symlinked to
# `~/.ssh/id_rsa` in a prior, separate Bash call) contains no credential-
# path token in the command text this hook actually sees. A raw-text match
# with no filesystem resolution cannot catch it. Known gap, pinned by a
# regression test rather than solved, consistent with this repo's existing
# disclosure convention for hook limitations.
#
# Personal/org-specific path additions: an optional
# ~/.claude/credential-file-guard.md, one glob per line (same grammar as
# data-file-read-guard.md), matched as a substring against the command text
# in addition to the built-in regex. See docs/security-hardening.md.
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

# The grep -E match against _LIB_CREDENTIAL_PATH_REGEX needs no independent
# timeout beyond _lib_jq's existing 5s backstop — it operates on an
# in-memory string, not the filesystem. Case-folded (-i): on the default
# case-insensitive-but-case-preserving filesystem this codebase already
# treats as a primary target (macOS APFS/HFS+, Windows NTFS), `id_RSA` or
# `.NETRC` opens the identical on-disk file as the canonical-case path, so
# a case-sensitive match here would be a full, silent bypass of a gate
# whose stated design is "no bypass valve."
if printf '%s' "$COMMAND" | grep -qEi "$_LIB_CREDENTIAL_PATH_REGEX"; then
  emit_deny "Blocked by credential-path Bash gate: the command references a credential-shaped path (an SSH private key, .netrc/_netrc, .git-credentials, a cloud credential store, or a non-template .env/credentials.json path). Reading, copying, or otherwise touching a credential file through Bash pulls its content toward Claude's conversation context. No bypass valve — if this command is legitimate and does not expose file content (e.g. ssh-add, chmod, ssh -i), run it yourself via the ! shell escape instead of through Claude's Bash tool."
  exit 0
fi

# --- Personal/org-specific path additions from credential-file-guard.md --
CREDENTIAL_FILE_GUARD="${HOME}/.claude/credential-file-guard.md"
if [ -f "$CREDENTIAL_FILE_GUARD" ] && [ -r "$CREDENTIAL_FILE_GUARD" ]; then
  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    # Strip CR (CRLF), then leading/trailing whitespace.
    line=${raw_line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    [ -z "$line" ] && continue
    case "$line" in '#'*) continue ;; esac

    # shellcheck disable=SC2254 # $line is an intentional user-authored glob,
    # mirroring deny-data-file-reads.sh's config-glob loop. Quoting it forces
    # literal matching and would silently break every wildcard rule in every
    # user's guard file — a false negative on this deny gate.
    #
    # Case-folded (nocasematch), same rationale as the built-in regex match
    # above: on a case-insensitive-but-case-preserving filesystem, a
    # case-varied command still opens the identical file the user's glob was
    # meant to flag, and bash `case` has no per-pattern case-fold syntax —
    # the only way to fold this match is the shopt, scoped tightly around
    # this one case statement and restored immediately after.
    shopt -s nocasematch
    case "$COMMAND" in
      *$line*)
        emit_deny "Blocked by credential-path Bash gate: the command matches the glob '${line}' in ~/.claude/credential-file-guard.md, a path shape you flagged as credential-bearing. (See docs/security-hardening.md.)"
        exit 0
        ;;
    esac
    shopt -u nocasematch
  done < "$CREDENTIAL_FILE_GUARD"
fi

exit 0
