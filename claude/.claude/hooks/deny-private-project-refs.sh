#!/bin/bash
# hook-class: gate
# Gate: reject `git commit`, `gh pr create`, `gh pr edit`, and mutating
# `gh api` calls if their content (staged diff, commit message, PR
# title/body, body-source file contents, gh-api JSON body, or
# referenced --input file) contains tracker-ID tokens that aren't on
# the open-source allowlist. Enforces the tracker-ID piece of the
# repo-root CLAUDE.md redaction rule ("Redact private-project-
# identifying content").
#
# Dispatch: wired on the PreToolUse `Bash` matcher with NO `if`-condition,
# so it runs for every Bash tool call and filters internally. A narrowing
# `if: "Bash(git commit *)"` would let ordinary, executable forms such as
# `git -c key=val commit` and `git -C <path> commit` slip past the early
# dispatch unscanned. Command detection word-walks each shell fragment
# (split on `&&`/`;`/`|`/`$()`/backticks) past global git flags, env-var
# prefixes, and gh subcommand flags written ahead of the subcommand, then
# exits immediately — before any git or scan work — when no gated surface
# (git commit / gh pr create|edit / mutating gh api) is present.
#
# Scope and limits:
# - Catches the mechanical category (tracker IDs shaped like [A-Z]{2,}-\d+).
# - Catches a second mechanical category when the user opts in via
#   ~/.claude/private-projects.md: a literal, case-insensitive
#   substring scan against entries in that user-local file. See the
#   "Deliberate scope" section below for the full design.
# - Catches six structural shapes, always on (not gated by
#   private-projects.md): an IPv4 literal, an SSH config-directory or
#   id_<algorithm> key path reference, a /Users/ or /home/ home-rooted
#   filesystem path, a 32+ hex-char or UUID-shaped identifier, an
#   internal-TLD hostname, and a Slack-channel-shaped reference (excluding
#   bare issue-number refs like issue #421). Regexes live in _lib.sh as
#   _LIB_IPV4_LITERAL_REGEX and its five siblings; see that file for the
#   exact TLD list and key-algorithm list.
# - Does NOT catch internal tool names, a custom-named SSH key with
#   neither shape above, an internal hostname on a TLD outside the list
#   in _lib.sh, a short git SHA, or other structural fingerprints. Those
#   require review discipline.
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
#   expansion the hook doesn't execute. The content scan sees only the
#   literal `$(...)` string.
# - Wrapper forms that evaluate the real command behind a nested shell
#   boundary — `eval "git commit ..."`, `xargs git commit`,
#   `sh -c 'git commit ...'`. The command word lives inside a quoted
#   string the hook does not execute; same unscannable class as the
#   `$(...)` body case above.
# - A backslash-escaped `\git` invocation (used to bypass a shell alias)
#   is not recognized as a git command: fragment detection matches a
#   word equal to `git` or ending in `/git`, and `\git` is neither.
#   Closing this belongs in a _lib.sh change — the word test is shared
#   code used by several hooks.
# - `git -C <path> commit` aimed at a *different* repository is still
#   detected as a commit, but the staged-diff scan runs against the
#   session's current repository, not the `-C` target. `-C` into a
#   subdirectory of the same repo is unaffected (scan pathspecs are
#   repo-root-relative).
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
# - `gh issue create` and `gh issue comment` publish content the same
#   way `gh pr create`/`gh api` do, but this hook's dispatch
#   (IS_GIT_COMMIT / IS_GH_PR / IS_GH_API) has no branch recognizing
#   `gh issue` at all, so content posted that way is never scanned.
#   A different flag surface (`--body` inline text, not `-f`/`-F`
#   field-value files) than the three surfaces above, so closing this
#   is real, separate work, not a one-line fix.
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
# Blocklist deny message: names each matched blocklist entry verbatim
# and quotes the offending line(s) from the scanned content (capped at
# 3 lines per entry, each truncated to 200 chars). The matched token is
# the user's own private-project name, already in the staged content
# and in ~/.claude/private-projects.md; naming it in the deny
# discloses it to no new party while letting the agent remove it in
# one pass rather than bisecting the diff manually.
#
# Allowlist extension: append to OSS_ALLOWLIST below if a legitimate
# open-source prefix is blocked. Do NOT add private-project-specific
# prefixes.
#
# Shape-aware deny hint.
# ----------------------
# When the command chains operations with && or ||, the deny messages
# (both tracker-ID and private-projects branches) append a hint suggesting
# the agent split the chain into separate Bash calls. Detection is
# best-effort grep (not a real shell parser) — && and || in prose are
# rare enough to justify detection; ; is excluded to avoid false positives
# on prose semicolons. The hint is informational only — the deny condition
# is unchanged. The hint uses placeholder examples (<name>) in its
# illustrative cd-path guidance; it does not echo $COMMAND because the
# command text is not needed for the split guidance. Parallel to
# cwd_anchor_note_if_chained in require-worktree-for-git-writes.sh.

set -uo pipefail

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

# emit_deny is defined before sourcing _lib.sh so a missing _lib.sh can
# still deny rather than silently allow.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by redaction gate: could not source _lib.sh — hook cannot evaluate command detection safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by redaction gate: could not parse tool-input JSON. Refusing to evaluate redaction under malformed input."

# Defense-in-depth: only act on Bash calls. settings.json already matches
# the Bash tool, but the hook does not rely on that alone (see repo
# CLAUDE.md, "Hook defense-in-depth").
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Word-walk a single shell fragment and report which gated `gh` surface it
# invokes: `pr` for `gh pr create` / `gh pr edit`, `api` for `gh api`, and
# empty for everything else (non-gh fragments and non-gated gh subcommands
# such as `gh pr comment`). The `gh` word test (`gh` or `*/gh`) mirrors
# _lib_fragment_invokes_git, so absolute paths and env-var prefixes are
# seen through.
#
# gh's root command has no value-taking global flags (only `--help` /
# `--version`), but cobra still lets a subcommand's own flags be written
# *before* the subcommand — `gh -X POST api ...` and `gh --repo o/r pr
# create ...` both parse. So the walk cannot assume the first bare word
# after `gh` is the subcommand. Instead it keys on the command path:
#   - `pr` surface: a word `pr` immediately followed by `create` / `edit`.
#     A two-word command path is always contiguous (cobra resolves it as a
#     unit); hoisted flags land before `pr` or after `create`/`edit`, never
#     between them.
#   - `api` surface: any word `api` after `gh`. A bare `api` that is really
#     a flag value rather than the subcommand is harmless — the caller's
#     body-flag check still has to pass before IS_GH_API is set.
# Globbing is disabled so wildcards in the command text do not expand.
# Trailing non-[alnum/_/-] is stripped from each word (fragment splitting
# can leave `create)` from a paren group).
fragment_gh_gated_surface() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local past_gh=false prev="" word stripped surface=""
  for word in $fragment; do
    if ! $past_gh; then
      case "$word" in
        gh|*/gh) past_gh=true ;;
      esac
      continue
    fi
    stripped="${word%%[^a-zA-Z0-9_-]*}"
    case "$stripped" in
      create|edit) if [ "$prev" = "pr" ]; then surface="pr"; break; fi ;;
      api) surface="api"; break ;;
    esac
    prev="$stripped"
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  printf '%s' "$surface"
}

# Identify which gated surface(s) the command touches. A single chained
# command can touch several (`git commit ... && gh pr create ...`,
# `gh pr edit ... && gh api ... -X POST`); every matching scan path runs.
# Detection word-walks each shell fragment so global git flags (`-c`,
# `-C`, `--git-dir`, ...), gh subcommand flags written ahead of the
# subcommand, env-var prefixes, absolute paths, and
# `&&`/`;`/`|`/`$()`/backtick chains cannot hide the command word.
IS_GIT_COMMIT=0
IS_GH_PR=0
IS_GH_API=0
while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue
  if _lib_fragment_invokes_git "$fragment" \
      && [ "$(_lib_extract_git_subcmd "$fragment")" = "commit" ]; then
    IS_GIT_COMMIT=1
  fi
  case "$(fragment_gh_gated_surface "$fragment")" in
    pr)
      IS_GH_PR=1
      ;;
    api)
      # `gh api` defaults to GET but auto-promotes to POST whenever any
      # request-body flag is supplied (per gh docs: "adding request
      # parameters will automatically switch the request method to
      # POST"). Scan only a body-bearing call: explicit `-X` / `--method`
      # POST/PATCH/PUT/DELETE (`-X POST`, `-X=POST`, `--method POST`,
      # `--method=POST`, bare `-XPOST`), or any `-f` / `-F` / `--field` /
      # `--raw-field` / `--input` flag. Dispatch keys on flags, not the
      # endpoint path — endpoint allowlisting is fragile, and the next
      # leak path could be a new endpoint. The flag check is global to the
      # command string, not scoped to the gh-api fragment — intentionally
      # fail-toward-scan: a false positive costs one redundant scan, a
      # false negative ships a leak. `[^A-Za-z]` is the trailing method
      # boundary (grep-portable substitute for `\b`).
      if printf '%s\n' "$COMMAND" \
          | grep -qiE '((-X|--method)(=|[[:space:]]+)|-X)(POST|PATCH|PUT|DELETE)([^A-Za-z]|$)'; then
        IS_GH_API=1
      elif printf '%s\n' "$COMMAND" \
          | grep -qE '(^|[[:space:]])(-f|-F|--field|--raw-field|--input)(=|[[:space:]]+)'; then
        IS_GH_API=1
      fi
      ;;
  esac
done <<< "$(_lib_split_fragments "$COMMAND")"

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
#                                 NIST, ECMA, ANSI, OSC
#   Public-project trackers:      GH (GitHub shorthand), BUG (bugzilla),
#                                 JEP / JDK (OpenJDK), LLVM, GCC, GPT
#                                 (OpenAI's public model-family name —
#                                 a vendor brand, not a technical constant,
#                                 so it accepts the same unbounded-digit
#                                 tradeoff as GH/BUG rather than a fixed set)
#   Technical constants that      SHA, MD, HTTP, HTTPS, TLS, SSL, UTF
#   happen to match [A-Z]{2,}-\d+:
#   Designated placeholders:      PROJ, TICKET — reserved for examples
#                                 and docs; see repo CLAUDE.md
#                                 "Redact private-project-identifying
#                                 content" for the rationale.
OSS_ALLOWLIST='^(CVE|CWE|RFC|PEP|ISO|IETF|W3C|NIST|ECMA|ANSI|OSC|AIP|GH|BUG|JEP|JDK|LLVM|GCC|GPT|SHA|MD|HTTP|HTTPS|TLS|SSL|UTF|PROJ|TICKET)-'

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

# Detect && / || chain operators in the command and append a corrective
# hint to deny messages when the chain shape is present. ; is excluded —
# it appears too often in English prose (commit messages, PR descriptions)
# to be useful as a chain signal. Detection is best-effort grep: && or ||
# inside a string-quoted region (e.g. --body "use && to chain") would
# still trigger the hint (known false positive).
chain_split_hint_if_chained() {
  if printf '%s' "$1" | grep -qE '&&|\|\|'; then
    # $1 is used as a grep predicate only — its text is not echoed in
    # the hint because the command content is not needed for the split
    # guidance.
    printf '%s' " Tip: this command chains operations with && / ||. If the matched token is in a path or setup portion of the chain (e.g. a \`cd /home/<name>/...\` prefix) and is NOT actually a private-project reference in the gated content, split the chain into two separate Bash calls — the cwd persists between calls in the same session, so running \`cd /path\` as one call and the gated command as a follow-up call keeps the path out of the gated command's text. If the match IS a real private-project reference in the gated content (PR body, commit message, gh api body), the chain-split won't help — rewrite the content instead. The match-anywhere foundation is intentional; see the hook header for design rationale."
  fi
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
    # nothing staged) preserve their historical "let git decide" pass —
    # sound now that deny-invisible-commit-content.sh denies every shape
    # that would otherwise commit content this empty-diff snapshot missed.
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
  # to mention a tracker token. deny-invisible-commit-content.sh is what
  # makes an empty staged diff at hook time actually mean an empty commit.
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
  emit_deny "Commit blocked by redaction gate: the staged diff, commit message, referenced commit-message file, PR title, PR body, referenced body-source file, gh api request body, or referenced --input file contains tracker-ID tokens that may reveal a private project: ${HIT_LIST}. See repo CLAUDE.md section 'Redact private-project-identifying content'. If the match is an open-source reference or technical constant not on the allowlist, add the prefix to the OSS_ALLOWLIST variable in ~/.claude/hooks/deny-private-project-refs.sh. Otherwise rewrite the commit message / staged content / PR body / gh api body without the tracker ID before retrying.$(chain_split_hint_if_chained "$COMMAND")"
  exit 0
fi

# Six structural-shape detectors: IPv4 literal, SSH key path reference,
# home-rooted path, long hex identifier, internal hostname, Slack-channel
# shape. Two-phase: a single combined-alternation pre-check below finds out
# whether any detector matches at all, then (only on a match) the per-detector
# loop is checked independently (not one alternation) so the deny message can
# name which detector fired. Regex constants live in _lib.sh so this scan and
# any future consumer share one definition.
#
# Deny message names the detector label only — never the matched substring.
# Deliberate divergence from the tracker-ID and blocklist branches above,
# which both echo matched content: a long hex identifier could be a live
# session ID, and an internal hostname or IPv4 literal is network-recon-value
# data — echoing either here would persist it into this session's transcript,
# same reasoning deny-pii-in-commits.sh states for its own label-only rule.
#
# Here-string (`<<<`), not `printf | grep`: under this file's `set -uo
# pipefail`, piping a large SCAN_TARGET into `grep -Eq` risks the first match
# SIGPIPE-ing the printf side, and pipefail would then report the pipeline's
# rightmost exit status — misreporting a clean match as a grep error. A
# here-string has no pipe to SIGPIPE.
#
# Fail closed on rc>=2 (a real grep engine error). This is stricter than the
# tracker-ID scan above, whose `|| true` swallows any grep error and so fails
# open on one — a pre-existing inconsistency between the two scans, not
# resolved here.
# Each label (left of the first `:`) must never itself contain a colon — the
# split below keys on the first colon only, so a colon in the label would be
# swallowed into it while the pattern (which may legitimately contain `:` via
# POSIX bracket classes like `[:alpha:]`) stays intact either way.
STRUCTURAL_DETECTORS=(
  "IPv4 literal:${_LIB_IPV4_LITERAL_REGEX}"
  "SSH key path reference:${_LIB_SSH_KEY_PATH_REFERENCE_REGEX}"
  "home-rooted path:${_LIB_HOME_ROOTED_PATH_REGEX}"
  "long hex identifier:${_LIB_LONG_HEX_IDENTIFIER_REGEX}"
  "internal hostname:${_LIB_INTERNAL_HOSTNAME_REGEX}"
  "Slack-channel shape:${_LIB_SLACK_CHANNEL_SHAPE_REGEX}"
)

# Combined alternation of all six patterns above, derived here (not
# hand-maintained as a second constant) so a future 7th detector added to
# STRUCTURAL_DETECTORS is automatically covered by the fast path below.
structural_combined_pattern=""
for detector_entry in "${STRUCTURAL_DETECTORS[@]}"; do
  detector_pattern="${detector_entry#*:}"
  if [ -z "$structural_combined_pattern" ]; then
    structural_combined_pattern="(${detector_pattern})"
  else
    structural_combined_pattern="${structural_combined_pattern}|(${detector_pattern})"
  fi
done

# Single fast-path grep across the combined pattern: on the common case (no
# detector matches), this replaces 6 subprocess spawns with 1; on a match, it
# falls through to the per-detector loop below unchanged to name the label.
structural_fastpath_rc=0
grep -Eq -- "$structural_combined_pattern" <<< "$SCAN_TARGET" || structural_fastpath_rc=$?
if [ "$structural_fastpath_rc" -eq 0 ]; then
  for detector_entry in "${STRUCTURAL_DETECTORS[@]}"; do
    detector_label="${detector_entry%%:*}"
    detector_pattern="${detector_entry#*:}"
    detector_rc=0
    grep -Eq -- "$detector_pattern" <<< "$SCAN_TARGET" || detector_rc=$?
    if [ "$detector_rc" -eq 0 ]; then
      emit_deny "Commit blocked by redaction gate: the staged diff, commit message, referenced commit-message file, PR title, PR body, referenced body-source file, gh api request body, or referenced --input file matches the '${detector_label}' pattern — a shape that can identify a specific machine, person, or private project without naming it directly. The matched text is not shown here: it may itself be sensitive (e.g. a live session ID or a real hostname), and echoing it would persist it into this session's transcript. Remove the offending content before retrying. See repo CLAUDE.md section 'Redact private-project-identifying content'.$(chain_split_hint_if_chained "$COMMAND")"
      exit 0
    elif [ "$detector_rc" -ge 2 ]; then
      emit_deny "Blocked by redaction gate: the '${detector_label}' detector failed to scan the gated content (grep exit ${detector_rc}) — failing closed. Unscanned content is exactly the leak vector this hook guards against."
      exit 0
    fi
  done
  emit_deny "Blocked by redaction gate: the structural-detector fast-path pre-check matched, but no individual detector in the follow-up loop confirmed which one — failing closed on this pattern-composition mismatch between the combined and per-detector regexes."
  exit 0
elif [ "$structural_fastpath_rc" -ge 2 ]; then
  emit_deny "Blocked by redaction gate: the structural-detector fast-path pre-check failed to scan the gated content (grep exit ${structural_fastpath_rc}) — failing closed. Unscanned content is exactly the leak vector this hook guards against."
  exit 0
fi

# Tracker-ID scan clean. Try the user-local private-projects blocklist.
# Fail-open: a contributor without the file works normally. The
# readability gate ([ -r ]) covers both absent and unreadable cases.
# Union, not swap: $(_lib_config_dir)'s copy wins if present, else the legacy $HOME/.claude location -- keeps an already-armed CLAUDE_CONFIG_DIR user's guard live.
# An unresolvable config dir leaves PRIVATE_PROJECTS_FILE at the legacy path; this is an opt-in guard, not a gate, so resolver failure must not disable it.
PRIVATE_PROJECTS_FILE="${HOME}/.claude/private-projects.md"
if config_dir=$(_lib_config_dir) && [ -f "$config_dir/private-projects.md" ]; then
  PRIVATE_PROJECTS_FILE="$config_dir/private-projects.md"
fi
if [ -r "$PRIVATE_PROJECTS_FILE" ]; then
  blocklist_report=""
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
    # tradeoff rationale. Collect the offending lines (cap 3 per entry);
    # grep | head may SIGPIPE under pipefail — bare assignment is safe
    # because -e is not set.
    matched_lines=$(printf '%s' "$SCAN_TARGET" | grep -iw -F -- "$line" | head -3)
    if [ -n "$matched_lines" ]; then
      blocklist_report="${blocklist_report}"$'\n'"  - ${line}"
      while IFS= read -r hit; do
        if [ "${#hit}" -gt 200 ]; then
          hit="${hit:0:200}…"
        fi
        blocklist_report="${blocklist_report}"$'\n'"    ${hit}"
      done <<< "$matched_lines"
    fi
  done < "$PRIVATE_PROJECTS_FILE"

  if [ -n "$blocklist_report" ]; then
    emit_deny "Blocked by redaction gate: staged/committed content matches entries from your ~/.claude/private-projects.md blocklist. Remove these references before retrying:${blocklist_report}

See repo CLAUDE.md section 'Redact private-project-identifying content'.$(chain_split_hint_if_chained "$COMMAND")"
    exit 0
  fi
fi
