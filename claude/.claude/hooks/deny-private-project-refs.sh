#!/bin/bash
# Gate: reject `git commit`, `gh pr create`, `gh pr edit`, and mutating
# `gh api` calls if their content (staged diff, commit message, PR
# title/body, body-source file contents, gh-api JSON body, or
# referenced --input file) contains tracker-ID tokens that aren't on
# the open-source allowlist. Enforces the tracker-ID piece of the
# repo-root CLAUDE.md redaction rule ("Redact private-project-
# identifying content").
#
# NOTE — `if`-dispatch is advisory; the real gate is the internal regex
# at the top of this script. settings.json wires `if` entries
# (`Bash(git commit *)`, `Bash(gh pr create *)`, `Bash(gh pr edit *)`,
# `Bash(gh api *)`) for zero-cost early dispatch, but any drift between
# those patterns and the IS_GIT_COMMIT / IS_GH_PR / IS_GH_API regexes
# here creates silent coverage gaps. Update both surfaces when extending
# coverage.
#
# Scope and limits:
# - Catches the mechanical category (tracker IDs shaped like [A-Z]{2,}-\d+).
# - Catches a second mechanical category when the user opts in via
#   ~/.claude/private-projects.md: a literal, case-insensitive
#   substring scan against entries in that user-local file. See the
#   "Deliberate scope" section below for the full design.
# - Does NOT catch internal tool names, absolute filesystem paths with
#   private-project names, or structural fingerprints. Those require
#   review discipline.
# - Scans the full Bash command string so `git commit -m "..."`,
#   `gh pr create --body "..."`, `gh pr edit N --title "..."`,
#   `gh api ... -f body="..."`, and heredoc variants all get checked
#   without parsing the message out of shell quoting.
# - For `gh pr create/edit --body-file|--template <path>` (and short
#   forms `-F` / `-T`), reads the file and scans its contents. Fails
#   closed (blocks) if the path is not readable, or if the path is a
#   pseudo-file (`-`, `/dev/stdin`, `/dev/fd/*`, `/proc/*/fd/*`) whose
#   contents the hook cannot statically verify.
# - For `git commit -F <path>` / `--file <path>`, reads the
#   commit-message-source file and scans it under the same fail-closed
#   posture as gh pr body-source files. `git commit -m "..." -F <path>`
#   is the documented `-m` + `-F` concatenation form, both surfaces
#   scanned.
# - For mutating `gh api` calls — explicit `-X` / `--method`
#   POST/PATCH/PUT/DELETE (in any of `-X POST`, `-X=POST`, `-XPOST`,
#   `--method POST`, `--method=POST` forms) OR an implicit-POST call
#   that gh auto-promotes whenever any `-f` / `-F` / `--field` /
#   `--raw-field` / `--input` flag is supplied — scans the command
#   string (which already contains any `-f body="..."` /
#   `-F body="..."` literal field values), reads any `--input <path>`
#   JSON body file, AND reads any `-f key=@<path>` / `-F key=@<path>`
#   field-value file (which gh resolves at invocation time), again
#   fail-closed on pseudo-file or unreadable paths. Read-only `gh api`
#   calls (default GET, no body-bearing flags) are intentionally not
#   gated — they don't carry user-authored content into a body GitHub
#   re-publishes.
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
# - `gh api graphql` is a separate surface from the REST `gh api
#   <path>` calls covered above. A tracker-ID literal in `-f query=`
#   / `-F variables=` / `--input` is still caught by the same flag-
#   based dispatch and the same scans; the residual gap is persisted-
#   query / persisted-document idioms where the query is referenced
#   by ID and the actual content lives server-side. The hook cannot
#   inspect server-side state by design.
# - The `git commit` editor flow (`git commit` with neither `-m` nor
#   `-F`) populates `.git/COMMIT_EDITMSG` interactively after the
#   PreToolUse hook has already fired. Nothing for the hook to scan
#   at hook time.
#
# Deliberate scope: user-local private-projects blocklist.
# ---------------------------------------------------------
# A *committed* list of project names in this public repo would itself
# be the leak — hardcoding in cleartext the exact strings the rule
# prevents from shipping. That objection still stands; do not propose a
# committed blocklist.
#
# A *user-local* list at ~/.claude/private-projects.md is a different
# artifact: outside any repo, per-machine, never in git. The hook reads
# it at runtime, fails open if the file is absent or unreadable, and
# matches case-insensitive whole-word literals against the same
# SCAN_TARGET the tracker-ID scan inspects. Tracker-ID matches take
# priority — a commit with both gets the tracker-ID deny message.
#
# Whole-word matching (grep -w): the entry must be bordered by non-
# word characters on each side. So `Acme` matches the standalone word
# `Acme` (in any casing under -i) but NOT `AcmeCorp` (continuation),
# `acmebrand` (concatenation), or `acme` inside `today` (substring).
# The tradeoff vs. plain substring match: lower false-positive rate on
# common-substring entries, at the cost of missing concatenated
# identifiers (which the user can blocklist as separate entries if
# they appear in commit-time content).
#
# Invariant: the blocklist scan's deny message does NOT name the
# matched entry. Echoing a name the user explicitly flagged as
# sensitive would re-expose it in terminal output, screenshots, CI
# logs, and Claude's conversation context — exactly the surfaces this
# gate exists to protect. Generic-message-only is load-bearing and
# tested.
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
# chained command can touch multiple
# (`git commit ... && gh pr create ...`,
# `gh pr edit ... && gh api ... -X POST`), in which case all matching
# scan paths run.
IS_GIT_COMMIT=0
IS_GH_PR=0
IS_GH_API=0
if printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'; then
  IS_GIT_COMMIT=1
fi
if printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*gh\s+pr\s+(create|edit)(\s|$)'; then
  IS_GH_PR=1
fi
# `gh api` defaults to GET, but auto-promotes to POST whenever any
# request-body flag is supplied. Per gh's docs: "adding request
# parameters will automatically switch the request method to POST."
# So a call worth scanning is any of:
#  - explicit `-X` / `--method` POST/PATCH/PUT/DELETE (`-X POST`, `-X=POST`,
#    `--method POST`, `--method=POST`, and the bare `-XPOST` short-flag
#    concatenation gh accepts)
#  - any `-f` / `-F` / `--field` / `--raw-field` / `--input` flag (each
#    of which auto-POSTs even with no `-X`)
# Gate dispatch on flags, not on the endpoint shape — endpoint
# allowlisting is fragile and the next leak path could be a new
# endpoint. `[^A-Za-z]` is used as the trailing method boundary
# instead of `\b` for grep-portability and style consistency with the
# `(\s|$)` end-anchors on the IS_GIT_COMMIT and IS_GH_PR regexes.
# Note: the body-flag check is global to the command string, not
# scoped to the gh api segment of a chained command — a `-X POST`
# elsewhere in the chain would also dispatch the gh api branch. This
# is intentionally fail-toward-scan; a false positive on dispatch
# costs one redundant scan, a false negative ships a leak.
if printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*gh\s+api(\s|$)'; then
  if printf '%s\n' "$COMMAND" \
      | grep -qiE '((-X|--method)(=|[[:space:]]+)|-X)(POST|PATCH|PUT|DELETE)([^A-Za-z]|$)'; then
    IS_GH_API=1
  elif printf '%s\n' "$COMMAND" \
      | grep -qE '(^|[[:space:]])(-f|-F|--field|--raw-field|--input)(=|[[:space:]]+)'; then
    IS_GH_API=1
  fi
fi

if [ "$IS_GIT_COMMIT" -eq 0 ] && [ "$IS_GH_PR" -eq 0 ] && [ "$IS_GH_API" -eq 0 ]; then
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
#   Designated placeholders:      PROJ, TICKET — reserved for examples
#                                 and docs; see repo CLAUDE.md
#                                 "Redact private-project-identifying
#                                 content" for the rationale.
OSS_ALLOWLIST='^(CVE|CWE|RFC|PEP|ISO|IETF|W3C|NIST|ECMA|ANSI|GH|BUG|JEP|JDK|LLVM|GCC|SHA|MD|HTTP|HTTPS|TLS|SSL|PROJ|TICKET)-'

# Extract paths passed to any gh-pr body-source flag. Covers:
#   --body-file <path>    --body-file=<path>
#   -F <path>             -F=<path>
#   --template <path>     --template=<path>
#   -T <path>             -T=<path>
# One path per output line. Uses xargs tokenization so flag-like text
# inside a quoted argument value (e.g. a PR title containing "-F") is
# part of a multi-word token and is never matched as a standalone flag.
# xargs strips outer quotes from real file-path arguments, so quoted
# paths (e.g. --body-file "/path/body.md") are extracted correctly.
# Paths containing whitespace are not supported — xargs emits them as
# multiple tokens; the resulting path fails the readability check below
# and the hook fail-closes with a clear message. xargs failure is
# suppressed; returns empty on error (fail-open for unparseable input,
# consistent with the documented shell-expansion gap).
extract_body_source_paths() {
  local cmd="$1"
  printf '%s\n' "$cmd" | xargs -n1 2>/dev/null | awk '
    BEGIN { cap = 0 }
    cap { print; cap = 0; next }
    /^(--body-file|--template|-F|-T)$/ { cap = 1; next }
    /^(--body-file=|--template=|-F=|-T=)/ { sub(/^[^=]*=/, ""); print }
  '
}

# Extract paths passed to any git-commit message-source flag. Covers:
#   -F <path>             -F=<path>
#   --file <path>         --file=<path>
# Same tokenization behavior as extract_body_source_paths. Note that
# `git commit -F` and `gh pr create -F` both use the `-F` short form
# for "file with text" — they refer to different files, but the union
# of both flag sets is what each scan path needs to read; in a chained
# command the same path may appear via both extractors and get scanned
# twice (cheap, harmless).
extract_commit_message_source_paths() {
  local cmd="$1"
  printf '%s\n' "$cmd" | xargs -n1 2>/dev/null | awk '
    BEGIN { cap = 0 }
    cap { print; cap = 0; next }
    /^(--file|-F)$/ { cap = 1; next }
    /^(--file=|-F=)/ { sub(/^[^=]*=/, ""); print }
  '
}

# Extract paths passed to gh-api request-body-source flag. Covers:
#   --input <path>        --input=<path>
# Inline `-f body="..."` / `-F body="..."` literal content already
# lives in the command string and is scanned via the SCAN_TARGET +=
# COMMAND step in the gh-api branch. The separate `@<path>` form
# (`-f body=@<path>` / `-F body=@<path>`) is a different surface — gh
# resolves the file at invocation time — and is extracted by
# extract_gh_api_field_at_paths below. Same xargs tokenization behavior
# as extract_body_source_paths.
extract_gh_api_input_paths() {
  local cmd="$1"
  printf '%s\n' "$cmd" | xargs -n1 2>/dev/null | awk '
    BEGIN { cap = 0 }
    cap { print; cap = 0; next }
    /^--input$/ { cap = 1; next }
    /^--input=/ { sub(/^--input=/, ""); print }
  '
}

# Extract paths passed via the `@<path>` field-value form on gh-api
# field flags. Covers:
#   -f key=@<path>        -f=key=@<path>
#   -F key=@<path>        -F=key=@<path>
#   --field key=@<path>   --field=key=@<path>
#   --raw-field key=@<path>   --raw-field=key=@<path>
# gh reads the file at invocation time and uses the contents as the
# field value, so a tracker token in the file ships in the request
# body identically to inline `-f key="..."`. The pseudo-file form
# `@-` reads stdin (rejected by is_pseudo_file_path).
# Field key must start with letter or underscore; whitespace inside
# the path truncates the same way as the other extractors. Same
# xargs tokenization behavior as extract_body_source_paths prevents
# false positives from `key=@`-shaped text inside quoted field values.
extract_gh_api_field_at_paths() {
  local cmd="$1"
  printf '%s\n' "$cmd" | xargs -n1 2>/dev/null | awk '
    BEGIN { cap = 0 }
    cap {
      if ($0 ~ /^[A-Za-z_][A-Za-z0-9_]*=@/) { sub(/^[^@]*@/, ""); print }
      cap = 0; next
    }
    /^(-f|-F|--field|--raw-field)$/ { cap = 1; next }
    /^(-f|-F|--field|--raw-field)=/ {
      val = $0; sub(/^[^=]*=/, "", val)
      if (val ~ /^[A-Za-z_][A-Za-z0-9_]*=@/) { sub(/^[^@]*@/, "", val); print val }
    }
  '
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
  # in test_hooks.py listing WIDGET / FOOCORP / NULLPROJ / EXAMPLECO /
  # BARCORP / FAKEPROJ as invented prefixes). Without this exclusion,
  # every commit that adds a new test case would be blocked by the
  # hook under test.
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

    # `-F <path>` / `--file <path>` reference an external commit-message
    # file whose contents are NOT in the command string (parallel to
    # gh pr's --body-file). Read each referenced file and append its
    # contents. Fail-closed if any referenced path is unreadable or is
    # a pseudo-file. Gated under the same non-empty staged-diff guard
    # as the -m scan above so that empty-staged-diff flows (--amend
    # without --allow-empty staged content, --allow-empty alone,
    # nothing staged) preserve their historical "let git decide" pass.
    COMMIT_MSG_SOURCES=$(extract_commit_message_source_paths "$COMMAND")
    if [ -n "$COMMIT_MSG_SOURCES" ]; then
      while IFS= read -r commit_msg_path; do
        [ -z "$commit_msg_path" ] && continue
        if is_pseudo_file_path "$commit_msg_path"; then
          emit_deny "git commit passes a message-source flag pointing at a pseudo-file path ('${commit_msg_path}'). The redaction gate cannot statically verify what git will read from there — '-' / '/dev/stdin' / '/dev/fd/*' resolve to the hook's own stdin or a process-specific fd, not git's future stdin. Inline the message with -m or prepare a real on-disk file. See repo CLAUDE.md section 'Redact private-project-identifying content'."
          exit 0
        fi
        if [ ! -r "$commit_msg_path" ]; then
          emit_deny "git commit references a message-source file at '${commit_msg_path}', but that path does not exist or is not readable from the hook. The redaction gate refuses to scan an unreadable message file (fail-closed) because unscanned content is exactly the leak vector this hook guards against. Create the file before running the git commit command, inline the content with -m, or — if the path contains whitespace or shell-expansion the hook did not parse — simplify the path. See repo CLAUDE.md section 'Redact private-project-identifying content'."
          exit 0
        fi
        COMMIT_MSG_CONTENT=$(cat "$commit_msg_path" 2>/dev/null || true)
        SCAN_TARGET+=$'\n'"$COMMIT_MSG_CONTENT"
      done <<< "$COMMIT_MSG_SOURCES"
    fi
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

if [ "$IS_GH_API" -eq 1 ]; then
  # Inline `-f body="..."` / `-F body="..."` literal field values live
  # in the command string already; adding COMMAND once covers them.
  # Endpoint path and method flags are also in here, but the
  # tracker-ID regex only matches `[A-Z]{2,}-\d+` so the endpoint
  # shape is irrelevant to the false-positive surface.
  SCAN_TARGET+=$'\n'"$COMMAND"

  # `--input <path>` references an external JSON body file whose
  # contents are NOT in the command string. Read each referenced file
  # and append its contents. Fail-closed if any referenced path is
  # unreadable or is a pseudo-file.
  GH_API_INPUT_SOURCES=$(extract_gh_api_input_paths "$COMMAND")
  if [ -n "$GH_API_INPUT_SOURCES" ]; then
    while IFS= read -r gh_api_input_path; do
      [ -z "$gh_api_input_path" ] && continue
      if is_pseudo_file_path "$gh_api_input_path"; then
        emit_deny "gh api command passes --input pointing at a pseudo-file path ('${gh_api_input_path}'). The redaction gate cannot statically verify what gh will read from there — '-' / '/dev/stdin' / '/dev/fd/*' resolve to the hook's own stdin or a process-specific fd, not gh's future stdin. Inline the body with -f / -F field flags or prepare a real on-disk file. See repo CLAUDE.md section 'Redact private-project-identifying content'."
        exit 0
      fi
      if [ ! -r "$gh_api_input_path" ]; then
        emit_deny "gh api command references --input file at '${gh_api_input_path}', but that path does not exist or is not readable from the hook. The redaction gate refuses to scan an unreadable input file (fail-closed) because unscanned content is exactly the leak vector this hook guards against. Create the file before running the gh api command, inline the content with -f / -F field flags, or — if the path contains whitespace or shell-expansion the hook did not parse — simplify the path. See repo CLAUDE.md section 'Redact private-project-identifying content'."
        exit 0
      fi
      GH_API_INPUT_CONTENT=$(cat "$gh_api_input_path" 2>/dev/null || true)
      SCAN_TARGET+=$'\n'"$GH_API_INPUT_CONTENT"
    done <<< "$GH_API_INPUT_SOURCES"
  fi

  # `-f key=@<path>` / `-F key=@<path>` (and --field / --raw-field
  # long forms) read the field value from a file at gh-invocation
  # time. The literal `key=@<path>` lives in the command string and
  # passes the tracker-ID scan trivially (no tracker shape there);
  # the file content does not. Read each referenced file and append
  # its contents. Same fail-closed posture as --input.
  GH_API_FIELD_AT_SOURCES=$(extract_gh_api_field_at_paths "$COMMAND")
  if [ -n "$GH_API_FIELD_AT_SOURCES" ]; then
    while IFS= read -r gh_api_field_at_path; do
      [ -z "$gh_api_field_at_path" ] && continue
      if is_pseudo_file_path "$gh_api_field_at_path"; then
        emit_deny "gh api command passes a -f / -F / --field / --raw-field value of the form key=@PATH where PATH is a pseudo-file ('${gh_api_field_at_path}'). The redaction gate cannot statically verify what gh will read from there — '-' / '/dev/stdin' / '/dev/fd/*' resolve to the hook's own stdin or a process-specific fd, not gh's future stdin. Inline the value or use a real on-disk file. See repo CLAUDE.md section 'Redact private-project-identifying content'."
        exit 0
      fi
      if [ ! -r "$gh_api_field_at_path" ]; then
        emit_deny "gh api command references a -f / -F field-value file at '${gh_api_field_at_path}' (key=@PATH form), but that path does not exist or is not readable from the hook. The redaction gate refuses to scan an unreadable field-value file (fail-closed) because unscanned content is exactly the leak vector this hook guards against. Create the file before running the gh api command, inline the value, or — if the path contains whitespace or shell-expansion the hook did not parse — simplify the path. See repo CLAUDE.md section 'Redact private-project-identifying content'."
        exit 0
      fi
      GH_API_FIELD_AT_CONTENT=$(cat "$gh_api_field_at_path" 2>/dev/null || true)
      SCAN_TARGET+=$'\n'"$GH_API_FIELD_AT_CONTENT"
    done <<< "$GH_API_FIELD_AT_SOURCES"
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

if [ -n "$HITS" ]; then
  # Report the first few offenders to keep the message short.
  HIT_LIST=$(printf '%s' "$HITS" | head -5 | tr '\n' ' ' | sed 's/ $//')
  emit_deny "Commit blocked by redaction gate: the staged diff, commit message, referenced commit-message file, PR title, PR body, referenced body-source file, gh api request body, or referenced --input file contains tracker-ID tokens that may reveal a private project: ${HIT_LIST}. See repo CLAUDE.md section 'Redact private-project-identifying content'. If the match is an open-source reference or technical constant not on the allowlist, add the prefix to the OSS_ALLOWLIST variable in ~/.claude/hooks/deny-private-project-refs.sh. Otherwise rewrite the commit message / staged content / PR body / gh api body without the tracker ID before retrying."
  exit 0
fi

# Tracker-ID scan clean. Try the user-local private-projects blocklist.
# Fail-open: a contributor without the file works normally. The
# readability gate ([ -r ]) covers both absent and unreadable cases.
PRIVATE_PROJECTS_FILE="${HOME}/.claude/private-projects.md"
if [ -r "$PRIVATE_PROJECTS_FILE" ]; then
  while IFS= read -r raw_line || [ -n "$raw_line" ]; do
    # Strip CR (CRLF), then leading/trailing whitespace.
    line=${raw_line%$'\r'}
    line="${line#"${line%%[![:space:]]*}"}"
    line="${line%"${line##*[![:space:]]}"}"
    # Skip blanks and `#` comments.
    [ -z "$line" ] && continue
    case "$line" in '#'*) continue ;; esac

    # `-F` is literal-match (no regex foot-guns), `-i` case-insensitive,
    # `-w` whole-word boundaries, `--` guards entries that happen to
    # start with `-`. See header "Whole-word matching" note for the
    # tradeoff rationale.
    if printf '%s' "$SCAN_TARGET" | grep -qiw -F -- "$line"; then
      # Generic message — see header "Invariant" note. The matched
      # entry is intentionally NOT named.
      emit_deny "Blocked by redaction gate: the staged diff, commit message, referenced commit-message file, PR title, PR body, referenced body-source file, gh api request body, or referenced --input file contains an entry from your ~/.claude/private-projects.md blocklist. Review the content and remove the project name before retrying. (The hook deliberately does not name which entry matched — printing it would re-expose the value in terminal output, CI logs, and Claude's conversation context, which is exactly what this gate exists to prevent.) See repo CLAUDE.md section 'Redact private-project-identifying content'."
      exit 0
    fi
  done < "$PRIVATE_PROJECTS_FILE"
fi
