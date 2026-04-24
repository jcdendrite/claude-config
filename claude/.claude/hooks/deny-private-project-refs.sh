#!/bin/bash
# Gate: reject `git commit`, `gh pr create`, and `gh pr edit` if their
# content (staged diff, commit message, PR title/body, or body-source
# file contents) contains tracker-ID tokens that aren't on the open-
# source allowlist. Enforces the tracker-ID piece of the repo-root
# CLAUDE.md redaction rule ("Redact private-project-identifying
# content").
#
# NOTE — `if`-dispatch is advisory; the real gate is the internal regex
# at the top of this script. settings.json wires three `if` entries
# (`Bash(git commit *)`, `Bash(gh pr create *)`, `Bash(gh pr edit *)`)
# for zero-cost early dispatch, but any drift between those patterns
# and the IS_GIT_COMMIT / IS_GH_PR regexes here creates silent coverage
# gaps. Update both surfaces when extending coverage.
#
# Scope and limits:
# - Catches the mechanical category (tracker IDs shaped like [A-Z]{2,}-\d+).
# - Does NOT catch private project names, internal tool names, absolute
#   filesystem paths with private-project names, or structural
#   fingerprints. Those require review discipline.
# - Scans the full Bash command string so `git commit -m "..."`,
#   `gh pr create --body "..."`, `gh pr edit N --title "..."`, and
#   heredoc variants all get checked without parsing the message out
#   of shell quoting.
# - For `gh pr create/edit --body-file|--template <path>` (and short
#   forms `-F` / `-T`), reads the file and scans its contents. Fails
#   closed (blocks) if the path is not readable, or if the path is a
#   pseudo-file (`-`, `/dev/stdin`, `/dev/fd/*`, `/proc/*/fd/*`) whose
#   contents the hook cannot statically verify.
#
# Known gaps (documented, not closed by this hook):
# - `gh pr create --fill|-f|--fill-first|--fill-verbose` sources the PR
#   body from commit messages. Since the git-commit gate already scans
#   each commit's message, this is safe ONLY if every commit went
#   through the Claude Code hook. Commits created outside the hook
#   (raw shell, IDE git GUI on a non-Claude session) can carry content
#   that `--fill` then republishes.
# - `gh pr create --body "$(cat file)"` or backtick command substitution
#   inside --body/--title hides the actual content behind shell
#   expansion the hook doesn't execute. Static regex match sees only
#   the literal `$(...)` string.
# - `git commit -F <file>` (commit message from file) parallels
#   `gh pr create --body-file` but is NOT scanned here. Pre-existing
#   gap, not addressed by this plan.
#
# Deliberate non-scope: private-project-name blocklist.
# ----------------------------------------------------
# A committed list of private-project names in this public repo *would
# itself be the leak* — it would hardcode in cleartext the exact
# strings the rule is trying to prevent from shipping. A local
# blocklist file sourced at runtime (e.g.
# ~/.claude/private-project-blocklist.txt) is technically viable but
# adds a maintenance surface and a second source of truth that can
# drift. Private-project-name defense stays with the repo-root
# CLAUDE.md "Redact private-project-identifying content" rule and
# reviewer discipline on all three surfaces (file content, commit
# message, PR title/description). Future contributors: do not
# re-propose a blocklist in this hook without first reading that
# reasoning.
#
# Allowlist extension: append to OSS_ALLOWLIST below if a legitimate
# open-source prefix is blocked. Do NOT add private-project-specific
# prefixes.

set -uo pipefail

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
JQ_EXIT=$?

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' \
    "$reason_json"
}

# Fail-closed on malformed input. Matches the posture of
# require-worktree-for-git-writes.sh: if we can't parse stdin, we can't
# tell what's about to run, so deny rather than silently allow.
if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "Blocked by redaction gate: could not parse tool-input JSON. Refusing to evaluate redaction under malformed input."
  exit 0
fi

# Identify which gated surface (if any) the command touches. A single
# chained command can touch both (`git commit ... && gh pr create ...`),
# in which case both scan paths run.
IS_GIT_COMMIT=0
IS_GH_PR=0
if printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  IS_GIT_COMMIT=1
fi
if printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*gh\s+pr\s+(create|edit)(\s|$)'; then
  IS_GH_PR=1
fi

if [ "$IS_GIT_COMMIT" -eq 0 ] && [ "$IS_GH_PR" -eq 0 ]; then
  exit 0
fi

REPO_ROOT=$(git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# Scope: this redaction gate exists to protect the claude-config repo,
# where accidental references to private projects would leak publicly.
# Other repos legitimately reference their own tracker IDs. Short-circuit
# unless origin.url looks like claude-config. `git config --get` returns
# empty (not an error exit) when the remote is missing, so the substring
# check safely handles the no-remote case too.
REMOTE_URL=$(git config --get remote.origin.url 2>/dev/null)
if [[ "$REMOTE_URL" != *claude-config* ]]; then
  exit 0
fi

# Allowlist: prefixes that are NEVER private-project tracker IDs.
# Extend by prefix (no digits). Organized by category so it's obvious
# what belongs here.
#   OSS specs / standards bodies: CVE, CWE, RFC, PEP, ISO, IETF, W3C,
#                                 NIST, ECMA, ANSI
#   Public-project trackers:      GH (GitHub shorthand), BUG (bugzilla),
#                                 JEP / JDK (OpenJDK), LLVM, GCC
#   Technical constants that      SHA, MD, HTTP, HTTPS, TLS, SSL
#   happen to match [A-Z]{2,}-\d+:
OSS_ALLOWLIST='^(CVE|CWE|RFC|PEP|ISO|IETF|W3C|NIST|ECMA|ANSI|GH|BUG|JEP|JDK|LLVM|GCC|SHA|MD|HTTP|HTTPS|TLS|SSL)-'

# Extract paths passed to any gh-pr body-source flag. Covers:
#   --body-file <path>    --body-file=<path>
#   -F <path>             -F=<path>
#   --template <path>     --template=<path>
#   -T <path>             -T=<path>
# One path per output line. Strips matching outer single or double
# quotes. Does NOT handle paths containing whitespace — such a path is
# silently truncated at the first space, fails the readability check
# below, and the hook fail-closes with a clear message.
extract_body_source_paths() {
  local cmd="$1"
  printf '%s\n' "$cmd" \
    | grep -oE '(--body-file|--template|-F|-T)(=|[[:space:]]+)[^[:space:];&|]+' \
    | sed -E 's/^(--body-file|--template|-F|-T)(=|[[:space:]]+)//' \
    | sed -E "s/^['\"](.*)['\"]$/\\1/"
}

# Pseudo-file paths whose contents the hook cannot meaningfully scan:
# the path either resolves to a different file at hook time than it
# will at gh-invocation time, or it's a process-specific fd reference
# that points into the hook's own stdin. Reject all of them fail-closed.
is_pseudo_file_path() {
  case "$1" in
    -|/dev/stdin|/dev/fd/*|/proc/*/fd/*) return 0 ;;
    *) return 1 ;;
  esac
}

# Each content surface that could carry a tracker ID is appended here
# separately so a future change won't silently drop coverage of one.
SCAN_TARGET=""

if [ "$IS_GIT_COMMIT" -eq 1 ]; then
  # Exclude the hook's own test file from the scan — tests of this hook
  # need synthetic tracker tokens as test data (see the header comment
  # in test_hooks.py listing WIDGET / FOOCORP / NULLCLIENT / EXAMPLECO /
  # BARCORP as invented prefixes). Without this exclusion, every commit
  # that adds a new test case would be blocked by the hook under test.
  # The `:(top,exclude)` pathspec magic is relative to the repo root so
  # this works regardless of the caller's cwd within the repo.
  STAGED_DIFF=$(git diff --cached -- ':(top,exclude)claude/.claude/hooks/tests/**' 2>/dev/null)
  if [ -n "$STAGED_DIFF" ]; then
    # Scan ONLY added lines in the diff, not removed ones. Without this,
    # a commit that *removes* a tracker ID (legitimate cleanup) would be
    # blocked because the deleted line still contains the token.
    # Exclude `+++ b/path` file headers; keep real `+` content lines.
    ADDED_LINES=$(printf '%s' "$STAGED_DIFF" | grep -E '^\+' | grep -vE '^\+\+\+' || true)
    SCAN_TARGET+=$'\n'"$ADDED_LINES"
    SCAN_TARGET+=$'\n'"$COMMAND"
  fi
  # When the staged diff is empty (amend-message-only, --allow-empty,
  # nothing staged, or only test-dir changes), the command is NOT added
  # to the scan target. This preserves historical behavior: let git
  # handle the no-content case on its own, even if the message happens
  # to mention a tracker token.
fi

if [ "$IS_GH_PR" -eq 1 ]; then
  # The command string already contains any inline `--body "..."` or
  # `--title "..."` value, so adding COMMAND once covers both. Kept
  # explicit here so the coverage story is visible at a glance.
  SCAN_TARGET+=$'\n'"$COMMAND"

  # `--body-file` / `--template` (and short forms) reference external
  # files whose contents are NOT in the command string. Read each
  # referenced file and append its contents. Fail-closed if any
  # referenced path is unreadable or is a pseudo-file.
  BODY_SOURCES=$(extract_body_source_paths "$COMMAND")
  if [ -n "$BODY_SOURCES" ]; then
    while IFS= read -r body_source_path; do
      [ -z "$body_source_path" ] && continue
      if is_pseudo_file_path "$body_source_path"; then
        emit_deny "gh pr command passes a body-source flag pointing at a pseudo-file path ('${body_source_path}'). The redaction gate cannot statically verify what gh will read from there — '-' / '/dev/stdin' / '/dev/fd/*' resolve to the hook's own stdin or a process-specific fd, not gh's future stdin. Inline the content with --body or prepare a real on-disk file. See repo CLAUDE.md section 'Redact private-project-identifying content'."
        exit 0
      fi
      if [ ! -r "$body_source_path" ]; then
        emit_deny "gh pr command references a body-source file at '${body_source_path}', but that path does not exist or is not readable from the hook. The redaction gate refuses to scan an unreadable body file (fail-closed) because unscanned content is exactly the leak vector this hook guards against. Create the file before running the gh pr command, inline the content with --body, or — if the path contains whitespace or shell-expansion the hook did not parse — simplify the path. See repo CLAUDE.md section 'Redact private-project-identifying content'."
        exit 0
      fi
      BODY_CONTENT=$(cat "$body_source_path" 2>/dev/null || true)
      SCAN_TARGET+=$'\n'"$BODY_CONTENT"
    done <<< "$BODY_SOURCES"
  fi
fi

if [ -z "$SCAN_TARGET" ]; then
  exit 0
fi

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

emit_deny "Commit blocked by redaction gate: the staged diff, commit message, PR title, PR body, or referenced body-source file contains tracker-ID tokens that may reveal a private project: ${HIT_LIST}. See repo CLAUDE.md section 'Redact private-project-identifying content'. If the match is an open-source reference or technical constant not on the allowlist, add the prefix to the OSS_ALLOWLIST variable in ~/.claude/hooks/deny-private-project-refs.sh. Otherwise rewrite the commit message / staged content / PR body without the tracker ID before retrying."
