#!/bin/bash
# hook-class: gate
# Gate: deny `git commit` when the commit's content — added lines of the
# staged diff, the commit message, or a referenced commit-message file —
# contains personally-identifying or protected health information (PII/PHI).
#
# Opt-in. Dormant unless ~/.claude/pii-patterns.md exists as a readable
# regular file. Every stow user gets this hook disabled by default; arming
# it is a deliberate per-machine action. See docs/security-hardening.md.
#
# Fires on every repo (no origin scoping) — a PII commit gate is only
# useful if it covers the repos that actually hold PII. This is the
# deliberate opposite of deny-private-project-refs.sh, which is scoped to
# the claude-config repo alone.
#
# Dispatch: wired on the PreToolUse `Bash` matcher with NO `if`-condition,
# so it runs for every Bash tool call and filters internally. A narrowing
# `if: "Bash(git commit *)"` would let ordinary, executable commit forms
# such as `git -c key=val commit` and `git -C <path> commit` slip past the
# early dispatch unscanned. The hook identifies the commit with _lib's
# word-walking subcommand extractor (which sees through global
# `-c`/`-C`/`--git-dir` flags and `&&`/`;`/`|` command chains), and exits
# immediately — before any git or scan work — whenever it is unarmed.
#
# Robust against `git commit --no-verify`: a Claude Code PreToolUse hook
# intercepts the Bash tool call itself. --no-verify disables only git's
# native pre-commit/commit-msg chain, not this hook.
#
# Pattern tiers (active once armed):
#  - Built-in generic patterns, shipped in this file: US Social Security
#    number (NNN-NN-NNNN) and credit-card-shaped 13-19 digit runs that
#    pass a Luhn checksum (the checksum cuts false positives on ordinary
#    long digit runs).
#  - User patterns from ~/.claude/pii-patterns.md: every `<label>: <regex>`
#    line. Environment-specific identifier shapes (MRN, internal UUID, and
#    similar) live only in that user-local file and never ship in this repo.
#
# Config-file grammar (~/.claude/pii-patterns.md), line-based, `#` comments
# and blank lines ignored:
#  - `<label>: <regex>`  — a labelled PII pattern. <regex> is POSIX ERE
#    (grep -E). <label> is a human-readable name used in the deny message
#    in place of the regex.
#  - `exclude: <glob>`   — a repo-relative path glob dropped from the diff
#    scan, for legitimate synthetic-PII test fixtures.
# A non-comment line with neither shape (no `:`, empty label, empty value,
# or an uncompilable regex) is a config error: the hook denies fail-closed
# and names the line number, because a silently-skipped pattern line is an
# unscanned leak vector. No timeout guards the user-pattern scan: grep -E
# evaluates POSIX ERE with a non-backtracking matcher and ERE has no
# backreferences, so a user pattern cannot trigger catastrophic-backtracking
# runtime.
#
# Diff surface: added lines (`^+`, excluding `+++` headers) of
# `git diff --cached`. When the command carries `-a`/`--all`, a `--`
# pathspec separator, or a bare pathspec argument, the added lines of
# `git diff HEAD` are scanned too — those forms commit working-tree
# content that is not in the index when this hook fires.
#
# Commit-message-source files: `-F <path>` / `--file <path>` are read and
# scanned. `-F -` / `/dev/stdin` / `/dev/fd/*` pseudo-files are rejected
# fail-closed — the hook cannot statically verify what git will read.
#
# Self-exclusion: claude/.claude/hooks/tests/** is always excluded from
# the diff scan (this hook's own synthetic-PII test fixtures live there).
#
# Deny message names the matched pattern by label only — never the matched
# value (it is PII; echoing it re-exposes it into the Claude transcript,
# which may be logged) and never the user regex text (a PII-shape regex is
# itself a structural fingerprint). Deliberate divergence from
# deny-env-reads.sh / deny-private-project-refs.sh, which name the path /
# token: those are safe to disclose, patient identifiers and fingerprint
# regexes are not.
#   The "never echo" rule covers scanned *content* — staged diff lines and
#   message-file text — which is not otherwise in the transcript. It does
#   NOT cover a path passed to -F/--file: that path is a command argument,
#   already present verbatim in the Bash tool call the agent issued, so the
#   -F deny messages below name it for diagnostics, disclosing it to no new
#   party even if the developer named the file after an identifier.
#
# Known gaps (documented, not closed):
#  - The editor-flow commit (`git commit` / `git commit --amend` with no
#    -m/-F) populates the message after the hook fires — nothing to scan
#    at hook time. Same gap as deny-private-project-refs.sh.
#  - A chained `git add ... && git commit` stages content after the hook
#    fires; the staged-diff scan sees the pre-add index. The commit
#    message is still scanned via the command string.
#  - Credit-card detection matches contiguous 13-19 digit runs only;
#    space- or dash-separated card numbers are not caught.
#  - `git -C <path> commit` aimed at a *different* repository is still
#    detected as a commit, but the staged-diff scan runs against the
#    session's current repository, not the `-C` target. `-C` into a
#    subdirectory of the same repo is unaffected (the scan pathspecs are
#    repo-root-relative).
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

# emit_deny is defined before sourcing _lib.sh so a missing _lib.sh can
# still deny rather than silently allow.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by PII commit gate: could not source _lib.sh — hook cannot evaluate the commit safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by PII commit gate: could not parse tool-input JSON. Refusing to evaluate the commit under malformed input."

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Opt-in: dormant unless the user-local pattern file exists as a readable
# regular file. Checked before any command parsing or git work so that an
# unarmed machine — the default — pays almost nothing per Bash call.
# `[ -f ]` follows symlinks and is true only for a regular file: a FIFO or
# device file at that path would block the line-by-line config read below,
# so a non-regular file is treated as not-armed.
PII_PATTERNS_FILE="${HOME}/.claude/pii-patterns.md"
if [ ! -f "$PII_PATTERNS_FILE" ] || [ ! -r "$PII_PATTERNS_FILE" ]; then
  exit 0
fi

# --- Detect `git commit`; decide whether `git diff HEAD` is also needed --
# `-a`/`--all`, a `--` pathspec separator, or a bare pathspec argument all
# commit working-tree content not in the index at hook time.
commit_fragment_has_worktree_target() {
  # Each stage is captured into a variable rather than run as one pipeline.
  # The awk exits as soon as it finds a worktree-target token; in a single
  # pipeline that closes the pipe on a still-running `xargs -n1`, and under
  # `set -o pipefail` the resulting SIGPIPE surfaces as a non-zero pipeline
  # status — spuriously reporting "no target". Testing the captured awk
  # output makes the verdict independent of any stage's exit code.
  local fragment_tokens verdict
  fragment_tokens=$(printf '%s\n' "$1" | xargs -n1 2>/dev/null)
  verdict=$(awk '
    BEGIN { past = 0; skip = 0 }
    {
      if (!past) { if ($0 == "commit") past = 1; next }
      if (skip) { skip = 0; next }
      if ($0 == "--") { print "Y"; exit }
      if ($0 ~ /^--/) {
        if ($0 == "--all") { print "Y"; exit }
        # Long options that consume a separate-token value.
        if ($0 ~ /^(--message|--file|--reuse-message|--reedit-message|--template|--author|--date|--cleanup|--fixup|--squash|--trailer|--pathspec-from-file)$/) skip = 1
        next
      }
      if ($0 ~ /^-./) {
        # Short-flag bundle. `a` anywhere means --all; a bundle ending in a
        # value-taking letter consumes the next token.
        if ($0 ~ /a/) { print "Y"; exit }
        if ($0 ~ /[mFCct]$/) skip = 1
        next
      }
      # A bare, non-consumed token after `commit` is a pathspec.
      print "Y"; exit
    }
  ' <<< "$fragment_tokens")
  [ "$verdict" = "Y" ]
}

# Walk the command's shell fragments once. _lib_split_fragments splits on
# &&/;/|/$()/backticks; _lib_extract_git_subcmd word-walks each fragment,
# skipping env-var prefixes and global git flags (-c, -C, --git-dir, ...),
# so `git -c key=val commit` and `GIT_DIR=x git commit` are recognised.
GIT_COMMIT_FOUND=0
HEAD_SCAN_NEEDED=0
while IFS= read -r git_fragment; do
  [ -z "$git_fragment" ] && continue
  _lib_fragment_invokes_git "$git_fragment" || continue
  [ "$(_lib_extract_git_subcmd "$git_fragment")" = "commit" ] || continue
  GIT_COMMIT_FOUND=1
  if commit_fragment_has_worktree_target "$git_fragment"; then
    HEAD_SCAN_NEEDED=1
  fi
done <<< "$(_lib_split_fragments "$COMMAND")"

if [ "$GIT_COMMIT_FOUND" -ne 1 ]; then
  exit 0
fi

# A `git commit` outside a work tree fails on its own; nothing to scan.
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  exit 0
fi

# --- Parse ~/.claude/pii-patterns.md -------------------------------------
# USER_LABELS[i] / USER_REGEXES[i] are parallel arrays; EXCLUDE_GLOBS holds
# `exclude:` paths. A malformed line denies fail-closed.
USER_LABELS=()
USER_REGEXES=()
EXCLUDE_GLOBS=()
config_lineno=0
while IFS= read -r raw_line || [ -n "$raw_line" ]; do
  config_lineno=$((config_lineno + 1))
  # Strip CR (CRLF), then leading/trailing whitespace.
  line=${raw_line%$'\r'}
  line="${line#"${line%%[![:space:]]*}"}"
  line="${line%"${line##*[![:space:]]}"}"
  [ -z "$line" ] && continue
  case "$line" in '#'*) continue ;; esac

  case "$line" in
    *:*) ;;
    *)
      emit_deny "Blocked by PII commit gate: ~/.claude/pii-patterns.md line ${config_lineno} is not a valid entry — every non-comment line must be '<label>: <regex>' or 'exclude: <glob>'. A line the hook cannot parse is an unscanned PII pattern, so the gate fails closed. Fix the line and retry."
      exit 0
      ;;
  esac

  config_label=${line%%:*}
  config_value=${line#*:}
  # Trim the label (trailing whitespace) and the value (leading whitespace).
  config_label="${config_label%"${config_label##*[![:space:]]}"}"
  config_value="${config_value#"${config_value%%[![:space:]]*}"}"
  if [ -z "$config_label" ] || [ -z "$config_value" ]; then
    emit_deny "Blocked by PII commit gate: ~/.claude/pii-patterns.md line ${config_lineno} has an empty label or empty value. Every non-comment line must be '<label>: <regex>' or 'exclude: <glob>'. The gate fails closed on an unparseable pattern line. Fix the line and retry."
    exit 0
  fi

  if [ "$config_label" = "exclude" ]; then
    EXCLUDE_GLOBS+=("$config_value")
    continue
  fi

  # Reject an uncompilable ERE. grep over an empty file exits 1 for a valid
  # regex (no match) and >=2 for a malformed one.
  grep -E -e "$config_value" /dev/null >/dev/null 2>&1
  if [ "$?" -ge 2 ]; then
    emit_deny "Blocked by PII commit gate: ~/.claude/pii-patterns.md line ${config_lineno} ('${config_label}') has a regex that grep -E cannot compile. The gate fails closed rather than silently skip a PII pattern. Fix the regex and retry."
    exit 0
  fi
  USER_LABELS+=("$config_label")
  USER_REGEXES+=("$config_value")
done < "$PII_PATTERNS_FILE"

# --- Extract `-F` / `--file` commit-message-source paths -----------------
# xargs tokenization mirrors deny-private-project-refs.sh: flag-like text
# inside a quoted value is one token and never matched as a standalone flag.
extract_commit_message_source_paths() {
  printf '%s\n' "$1" | xargs -n1 2>/dev/null | awk '
    BEGIN { cap = 0 }
    cap { print; cap = 0; next }
    /^(--file|-F)$/ { cap = 1; next }
    /^(--file=|-F=)/ { sub(/^[^=]*=/, ""); print }
  '
}

is_pseudo_file_path() {
  case "$1" in
    -|/dev/stdin|/dev/fd/*|/proc/*/fd/*) return 0 ;;
    *) return 1 ;;
  esac
}

# --- Build the scan target -----------------------------------------------
# Always exclude this hook's own synthetic-PII test fixtures, plus every
# user-configured `exclude:` glob. `:(top,exclude)` is repo-root-relative.
PATHSPEC_EXCLUDES=(':(top,exclude)claude/.claude/hooks/tests/**')
for exclude_glob in "${EXCLUDE_GLOBS[@]:-}"; do
  [ -z "$exclude_glob" ] && continue
  PATHSPEC_EXCLUDES+=(":(top,exclude)${exclude_glob}")
done

# The command string carries the commit message (`-m "..."`); scan it as-is.
SCAN_TARGET=$COMMAND

added_lines_of() {
  # Keep real `+` content lines, drop `+++` file headers.
  grep -E '^\+' | grep -vE '^\+\+\+' || true
}

STAGED_DIFF=$(git diff --cached -- "${PATHSPEC_EXCLUDES[@]}" 2>/dev/null)
SCAN_TARGET+=$'\n'"$(printf '%s' "$STAGED_DIFF" | added_lines_of)"

if [ "$HEAD_SCAN_NEEDED" -eq 1 ] && git rev-parse HEAD >/dev/null 2>&1; then
  HEAD_DIFF=$(git diff HEAD -- "${PATHSPEC_EXCLUDES[@]}" 2>/dev/null)
  SCAN_TARGET+=$'\n'"$(printf '%s' "$HEAD_DIFF" | added_lines_of)"
fi

COMMIT_MSG_SOURCES=$(extract_commit_message_source_paths "$COMMAND")
if [ -n "$COMMIT_MSG_SOURCES" ]; then
  while IFS= read -r msg_path; do
    [ -z "$msg_path" ] && continue
    if is_pseudo_file_path "$msg_path"; then
      emit_deny "Blocked by PII commit gate: git commit passes a message-source flag pointing at a pseudo-file path ('${msg_path}'). The gate cannot statically verify what git will read from '-' / '/dev/stdin' / '/dev/fd/*'. Inline the message with -m or use a real on-disk file."
      exit 0
    fi
    if [ ! -f "$msg_path" ] || [ ! -r "$msg_path" ]; then
      emit_deny "Blocked by PII commit gate: git commit references a message-source file at '${msg_path}', but that path is not a readable regular file from the hook. The gate refuses to scan it (fail-closed) — unscanned content is the leak vector this hook guards. Create the file, inline the message with -m, or simplify the path if it contains whitespace."
      exit 0
    fi
    SCAN_TARGET+=$'\n'"$(cat "$msg_path" 2>/dev/null || true)"
  done <<< "$COMMIT_MSG_SOURCES"
fi

# --- Luhn checksum for credit-card candidates ----------------------------
luhn_valid() {
  # Assignments are split across statements: a `local` builtin expands all
  # its argument RHS values before performing any assignment, so a single
  # `local digits=$1 parity=$((${#digits}...))` would read `digits` unset.
  local digits="$1"
  local sum=0 i d parity
  parity=$(( ${#digits} % 2 ))
  for (( i = 0; i < ${#digits}; i++ )); do
    d=${digits:i:1}
    if (( i % 2 == parity )); then
      d=$(( d * 2 ))
      (( d > 9 )) && d=$(( d - 9 ))
    fi
    sum=$(( sum + d ))
  done
  (( sum % 10 == 0 ))
}

# --- Scan -----------------------------------------------------------------
# Each match test reads SCAN_TARGET via a here-string, not `printf | grep`.
# `grep -q` exits on the first match; in a pipeline that SIGPIPEs the
# `printf`, and under `set -o pipefail` a large SCAN_TARGET could then make
# the pipeline report non-zero — dropping a real match. A here-string is
# not a pipeline, so the test reflects only grep's own exit status.
MATCHED_LABELS=()

if grep -qE '\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b' <<< "$SCAN_TARGET"; then
  MATCHED_LABELS+=("US Social Security number")
fi

CC_CANDIDATES=$(grep -oE '\b[0-9]{13,19}\b' <<< "$SCAN_TARGET" | sort -u)
if [ -n "$CC_CANDIDATES" ]; then
  while IFS= read -r cc_candidate; do
    [ -z "$cc_candidate" ] && continue
    if luhn_valid "$cc_candidate"; then
      MATCHED_LABELS+=("Credit card number")
      break
    fi
  done <<< "$CC_CANDIDATES"
fi

for i in "${!USER_REGEXES[@]}"; do
  if grep -qE -e "${USER_REGEXES[$i]}" <<< "$SCAN_TARGET"; then
    MATCHED_LABELS+=("${USER_LABELS[$i]}")
  fi
done

if [ "${#MATCHED_LABELS[@]}" -gt 0 ]; then
  LABEL_LIST=$(printf '%s\n' "${MATCHED_LABELS[@]}" | sort -u | tr '\n' ',' | sed 's/,/, /g; s/, $//')
  emit_deny "Commit blocked by PII guard: the staged diff, commit message, or a referenced commit-message file matches PII pattern(s): ${LABEL_LIST}. The matched values are not echoed here — they are PII. Remove the offending content before committing. For a legitimate synthetic-PII test fixture, add an 'exclude: <repo-relative-glob>' line to ~/.claude/pii-patterns.md rather than disarming the gate. See docs/security-hardening.md."
  exit 0
fi

exit 0
