#!/bin/bash
# Gate: reject `git commit` if the staged diff or the commit message on the
# command line contains tracker-ID tokens that aren't on the open-source
# allowlist. Enforces the tracker-ID piece of the repo-root CLAUDE.md
# redaction rule ("Redact client-identifying content").
#
# Scope and limits:
# - Catches the mechanical category (tracker IDs shaped like [A-Z]{2,}-\d+).
# - Does NOT catch client names, internal tool names, absolute filesystem
#   paths with client-names, or structural fingerprints. Those require
#   review discipline or a blocklist extension.
# - Scans the full Bash command string so `git commit -m "..."` and
#   heredoc variants both get checked without parsing the message out
#   of shell quoting.
#
# Allowlist extension: append to OSS_ALLOWLIST below if a legitimate
# open-source prefix is blocked. Do NOT add client-specific prefixes.

set -uo pipefail

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')

# Only gate git commit commands. Exit silently on anything else.
# Match `git commit` at the start of the command or after a shell
# separator, same pattern as require-code-review.sh.
if ! printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

STAGED_DIFF=$(git diff --cached 2>/dev/null)
if [ -z "$STAGED_DIFF" ]; then
  # Amend-message-only, --allow-empty, or nothing staged. Let git decide.
  exit 0
fi

# Scan ONLY added lines in the diff, not removed ones. Without this,
# a commit that *removes* a tracker ID (legitimate cleanup) would be
# blocked because the deleted line still contains the token.
# Exclude `+++ b/path` file headers; keep real `+` content lines.
ADDED_LINES=$(printf '%s' "$STAGED_DIFF" | grep -E '^\+' | grep -vE '^\+\+\+' || true)

# Scan target: added diff lines plus the full Bash command. The command
# string includes any `-m "..."` message or heredoc body.
SCAN_TARGET=$(printf '%s\n%s\n' "$ADDED_LINES" "$COMMAND")

# Allowlist: prefixes that are NEVER client tracker IDs. Extend by prefix
# (no digits). Organized by category so it's obvious what belongs here.
#   OSS specs / standards bodies: CVE, CWE, RFC, PEP, ISO, IETF, W3C,
#                                 NIST, ECMA, ANSI
#   Public-project trackers:      GH (GitHub shorthand), BUG (bugzilla),
#                                 JEP / JDK (OpenJDK), LLVM, GCC
#   Technical constants that      SHA, MD, HTTP, HTTPS, TLS, SSL
#   happen to match [A-Z]{2,}-\d+:
OSS_ALLOWLIST='^(CVE|CWE|RFC|PEP|ISO|IETF|W3C|NIST|ECMA|ANSI|GH|BUG|JEP|JDK|LLVM|GCC|SHA|MD|HTTP|HTTPS|TLS|SSL)-'

HITS=$(printf '%s' "$SCAN_TARGET" \
  | grep -oE '\b[A-Z]{2,}-[0-9]+\b' \
  | sort -u \
  | grep -vE "$OSS_ALLOWLIST" \
  || true)

if [ -z "$HITS" ]; then
  exit 0
fi

# Report the first few offenders to keep the message short.
HIT_LIST=$(printf '%s' "$HITS" | head -5 | tr '\n' ' ' | sed 's/ $//')

REASON="Commit blocked by redaction gate: the staged diff or commit message contains tracker-ID tokens that may reveal a client or private project: ${HIT_LIST}. See repo CLAUDE.md section 'Redact client-identifying content'. If the match is an open-source reference or technical constant not on the allowlist, add the prefix to the OSS_ALLOWLIST variable in ~/.claude/hooks/deny-client-refs.sh. Otherwise rewrite the commit message / staged content without the tracker ID before retrying."

REASON_JSON=$(printf '%s' "$REASON" | jq -Rs '.')

printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
