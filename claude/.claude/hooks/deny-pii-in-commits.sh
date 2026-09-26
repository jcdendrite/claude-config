#!/bin/bash
# hook-class: gate
# tier-threat-model: cooperative, irreversible
# Gate: deny `git commit` when the commit's content — added lines of the
# staged diff, the commit message, or a referenced commit-message file —
# contains personally-identifying or protected health information (PII/PHI),
# or a credential-shaped value (a GitHub token prefix, an AWS access key ID,
# a PEM private-key header).
#
# Two independent tiers: the credential-value check (below) is always on, no arming file. The SSN/credit-card built-ins and every user `<label>: <regex>` pattern stay dormant unless ~/.claude/pii-patterns.md exists as a readable regular file — arming that tier is still a deliberate per-machine action. See docs/security-hardening.md.
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
# immediately — before any git or scan work — whenever the command is not a
# commit at all.
#
# Robust against `git commit --no-verify`: a Claude Code PreToolUse hook
# intercepts the Bash tool call itself. --no-verify disables only git's
# native pre-commit/commit-msg chain, not this hook.
#
# Pattern tiers:
#  - Credential-value patterns (always on, unarmed or not): the shared
#    _LIB_CREDENTIAL_VALUE_REGEX (GitHub token prefixes, an AWS access key
#    ID, a PEM private-key header) also used by redact-credential-values.sh
#    — same near-zero false-positive risk that justifies deny-env-reads.sh's
#    always-on posture.
#  - Built-in generic PII patterns (active once armed): US Social Security
#    number (NNN-NN-NNNN) and credit-card-shaped 13-19 digit runs that
#    pass a Luhn checksum (the checksum cuts false positives on ordinary
#    long digit runs).
#  - User patterns from ~/.claude/pii-patterns.md (active once armed):
#    every `<label>: <regex>` line. Environment-specific identifier shapes
#    (MRN, internal UUID, and similar) live only in that user-local file
#    and never ship in this repo.
#
# Config-file grammar (~/.claude/pii-patterns.md), line-based, `#` comments
# and blank lines ignored — governs the two armed-only tiers only; the
# credential-value tier has no config of its own:
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
# scanned. `-F -` / `/dev/stdin` / `/dev/fd/*` / `/proc/*/fd/*` pseudo-files are rejected
# fail-closed — the hook cannot statically verify what git will read.
# Both apply only to the spellings the extractor recognises (see Known gaps).
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
#  - A chained `git add ... && git commit` staging content after this hook's
#    staged-diff scan is denied by deny-invisible-commit-content.sh, so that
#    shape does not reach this hook's PII/credential scan. That coverage is
#    bounded by deny-invisible-commit-content.sh's own Known gaps list.
#  - A `-F <path>` message file whose content changes after this hook fires
#    is not re-read. `cp secret.src msg.txt && git commit -F msg.txt` is
#    allowed when msg.txt is benign at hook time, by this hook and by
#    deny-invisible-commit-content.sh.
#  - A message that receives content through shell expansion is scanned as
#    literal text at hook time, so a credential arriving that way is not
#    seen. `git commit -m "$(cat f)"` and `M=$(cat f); git commit -m "$M"`
#    are allowed by this hook and by deny-invisible-commit-content.sh.
#  - Credit-card detection matches contiguous 13-19 digit runs only;
#    space- or dash-separated card numbers are not caught.
#  - An SSN or credit-card value written as an apostrophe-grouped thousands
#    numeral (`N'NNN'NNN`, or an SSN whose last group is `N'NNN`) is allowed
#    when stripping quotes would not join its digits to any other digit:
#    - This covers a C++ digit-separated integer literal.
#    - It also covers a quote splice at the same positions in unquoted
#      command text.
#    - Each occurrence is judged alone, so another copy of the value
#      elsewhere in the commit does not change its verdict.
#  - mask_thousands_numerals's sed -E expression is exercised only against
#    GNU sed (this repo's CI and most dev machines). BSD/macOS sed's
#    behavior on this expression is not independently checked.
#  - `git -C <path> commit` aimed at a *different* repository is still
#    detected as a commit, but the staged-diff scan runs against the
#    session's current repository, not the `-C` target. `-C` into a
#    subdirectory of the same repo leaves the diff scan unaffected: the diff
#    calls pass `-c diff.relative=false` and repo-root-relative pathspecs, so
#    the scan covers the whole repo whatever the hook's cwd or `diff.relative`
#    say. A relative `-F <path>` resolves against the hook's cwd, not the
#    commit's, so `git -C a commit -F msg.txt` scans `msg.txt` while git reads
#    `a/msg.txt`.
#  - Every _lib_strip_shell_quotes/_lib_split_fragments call site in this
#    hook checks its exit status and fails closed: the command's own
#    fragment split, each fragment's git_fragment_unquoted strip, the
#    SCAN_TARGET strip that feeds the credential-value/PII scan buffer, and
#    the SSN/credit-card union's mask_thousands_numerals call and its own
#    strip.
#  - The scanned repo, index, and work tree, and both probes' repo, come from
#    the hook's own cwd and environment, not the commit's effective ones.
#  - When the hook's cwd is not itself a work tree, a `git -C <repo>`,
#    `cd <repo> &&`, `--git-dir`, or `--work-tree` commit makes the probe
#    exit 128 and skip the scan.
#  - When the hook's cwd is a work tree, the same forms make the hook scan
#    that work tree's repo instead of the commit's.
#  - An env-prefix form before `git commit` redirects only the commit, not
#    the hook's scan:
#    - `GIT_DIR` redirects the repository.
#    - `GIT_WORK_TREE` redirects the work tree, which matters only for the
#      `-a`/pathspec scan of worktree content against HEAD.
#    - `GIT_INDEX_FILE` redirects the index, so a token present only in the
#      alternate index is allowed.
#  - A linked worktree of the same repo, committed to from the main
#    checkout, makes the diff scan read the wrong worktree's index.
#  - With an unborn HEAD and a worktree-target commit form (`-a`, `--`, or a
#    bare pathspec), worktree-vs-index content is not scanned.
#  - Status 128 is git's generic fatal status; the probes treat it as "not a
#    work tree" or "no HEAD" and skip without logging.
#  - A descendant of a capped git call that ignores SIGTERM and holds the
#    captured stdout pipe keeps the hook open past the cap, because the cap
#    bounds only the direct child.
#    The hook then stays open until the harness's own hook timeout.
#    A PreToolUse command hook that times out does not block the tool call,
#    so the commit then proceeds unscanned.
#    _lib_capped_for's header in _lib.sh quotes the vendor rule.
#  - The diff calls pass `--no-ext-diff` so a repo-local `diff.external`
#    driver is not such a descendant.
#  - A file git classifies as binary yields "Binary files differ" with no
#    added lines, so the credential-value scan does not see it.
#    Git classifies a file as binary through these routes:
#    - A `-diff` or `binary` attribute, including one from a committed
#      `.gitattributes` or one staged in the same commit as the file.
#    - A NUL byte in the first 8000 bytes of the content.
#    - A `diff.<driver>.binary=true` config setting.
#    - A `core.bigFileThreshold` smaller than the file. The default is 512 MiB,
#      so a large enough file needs no config.
#    These are examples, not a closed list.
#  - `--no-textconv` stops the scan from seeing text a textconv driver exposes
#    from a binary-classified file.
#  - An added line whose content starts with `++` renders as `+++...`, so the
#    `+++` file-header filter in `added_lines_of` drops it.
#  - GNU grep 3.11 drops an added line holding a byte that is invalid in the
#    hook's locale encoding, so a credential on such a line is not scanned.
#    Other greps (BSD/macOS) are unverified.
#  - Git's attached short form (`-Fpath`, `-F-`) is not recognised as a
#    message source, so this hook allows it without scanning the file or
#    rejecting the stdin source. deny-invisible-commit-content.sh allows it too.
#  - Git's unambiguous long-option abbreviations (`--fil=path`, `--fil=-`)
#    are likewise allowed by both hooks without a scan or a stdin rejection.
#  - A commit alias (`git config alias.ci commit`, then `git ci -m x`) is not
#    detected as a commit, so both hooks allow it.
#  - An attached short flag whose value ends in `m`, `F`, `C`, `c` or `t`
#    (`-Fmsg.txt` and `-mtext` match, `-mwip` and `-mfoo` do not) makes
#    `_lib_commit_fragment_has_worktree_target` read the next token as the
#    flag's value, so a following pathspec is not seen as a worktree target
#    and the worktree-content scan is skipped.
#  - `-F=path` is recognised and scanned as a file named `path`, but git reads
#    a file literally named `=path`, so a credential in that file is not scanned.
#  - `--pathspec-from-file` (both spellings) commits worktree content that
#    `_lib_commit_fragment_has_worktree_target` does not classify as a worktree target.
#  - `-p`, `--patch` and `--interactive` likewise commit worktree content
#    that `_lib_commit_fragment_has_worktree_target` does not classify.
#  - The fix for the command-line-parsing gaps above is to scan the committed
#    bytes rather than parse the command line.
#
# The `-F`/`--file` unreadable-source and pseudo-file checks below run for every commit, armed or not — fail-closed on content the hook cannot verify, independent of which scan tier triggered the commit-detection path. They cover only the spellings the extractor recognises: spaced `-F path`, `--file path`, and `--file=path`.
#
# Fail-closed on unparseable hook input.

set -uo pipefail

DENY_GATE_LABEL="PII commit"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

# emit_deny is defined before sourcing _lib.sh so a missing _lib.sh can
# still deny rather than silently allow.
if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh (run ./install.sh to pick up hook files this update added) — hook cannot evaluate the commit safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON. Refusing to evaluate the commit under malformed input."

# Defense-in-depth: only act on Bash calls (settings.json already matches Bash).
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# --- Detect `git commit`; decide whether `git diff HEAD` is also needed --
# Runs before the pii-patterns.md arming check below: the credential-value sub-check needs commit-detection and diff extraction regardless of arming.
# `-a`/`--all`, a `--` pathspec separator, or a bare pathspec argument all
# commit working-tree content not in the index at hook time — detected via
# _lib_commit_fragment_has_worktree_target (_lib.sh), shared with
# deny-invisible-commit-content.sh.

# Walk the command's shell fragments once. _lib_split_fragments splits on
# &&/;/|/$()/backticks; _lib_extract_git_subcmd word-walks each fragment,
# skipping env-var prefixes and global git flags (-c, -C, --git-dir, ...),
# so `git -c key=val commit` and `GIT_DIR=x git commit` are recognised.
GIT_COMMIT_FOUND=0
HEAD_SCAN_NEEDED=0
# Raw $COMMAND by design — see RAW_SPLIT_BY_DESIGN in
# test_hook_command_normalization.py: the raw copy must survive for
# _lib_commit_fragment_has_worktree_target's xargs tokenizer.
GIT_FRAGMENTS=$(_lib_split_fragments "$COMMAND")
GIT_FRAGMENTS_SPLIT_EXIT=$?
# Checked and fail-closed, matching deny-invisible-commit-content.sh's own
# SPLIT_EXIT pattern.
if [ "$GIT_FRAGMENTS_SPLIT_EXIT" -ne 0 ]; then
  emit_deny "Commit — could not split the command into fragments (exit ${GIT_FRAGMENTS_SPLIT_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned git commit."
  exit 0
fi
while IFS= read -r git_fragment; do
  [ -z "$git_fragment" ] && continue
  # Per-fragment strip, matcher calls only: an adjacent-quote split
  # (`"git" "commit"`) can't dodge _lib_fragment_invokes_git /
  # _lib_extract_git_subcmd, which are quote-blind by contract. Checked and
  # fail-closed, matching deny-invisible-commit-content.sh's own
  # COMMAND_UNQUOTED computation.
  git_fragment_unquoted=$(_lib_strip_shell_quotes "$git_fragment")
  git_fragment_unquoted_exit=$?
  if [ "$git_fragment_unquoted_exit" -ne 0 ]; then
    emit_deny "Commit — could not quote-strip a command fragment (exit ${git_fragment_unquoted_exit}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned git commit."
    exit 0
  fi
  _lib_fragment_invokes_git "$git_fragment_unquoted" || continue
  [ "$(_lib_extract_git_subcmd "$git_fragment_unquoted")" = "commit" ] || continue
  GIT_COMMIT_FOUND=1
  # Raw fragment, not the stripped copy: xargs below tokenizes quotes
  # itself, and pre-stripping splits a quoted -m message into words the
  # pathspec check reads as a worktree target.
  if _lib_commit_fragment_has_worktree_target "$git_fragment"; then
    HEAD_SCAN_NEEDED=1
  fi
done <<< "$GIT_FRAGMENTS"

if [ "$GIT_COMMIT_FOUND" -ne 1 ]; then
  exit 0
fi

# A `git commit` outside a work tree fails on its own; nothing to scan. Wrapped
# in _lib_capped (5s backstop), which runs unconditionally (armed or not).
# The probe skips only on 128, git's generic fatal status, read as "not a
# work tree".
# Every other nonzero status denies, including any status the wrapper itself
# produces on a cap kill, because exiting 0 here would silently skip the
# always-on credential-value tier along with everything else.
_lib_capped git rev-parse --is-inside-work-tree >/dev/null 2>&1
REV_PARSE_STATUS=$?
if [ "$REV_PARSE_STATUS" -eq 128 ]; then
  exit 0
fi
if [ "$REV_PARSE_STATUS" -ne 0 ]; then
  emit_deny "Commit — could not determine whether this is a git work tree (exit ${REV_PARSE_STATUS}) — the probe was killed at the scan cap or failed. Fail-closed — the guard cannot verify commit content is scannable without it."
  exit 0
fi

# Opt-in: dormant unless the user-local pattern file exists as a readable
# regular file. `[ -f ]` follows symlinks and is true only for a regular
# file: a FIFO or device file at that path would block the line-by-line
# config read below, so a non-regular file is treated as not-armed. Gates
# only the SSN/credit-card/user-pattern tier below — the credential-value
# sub-check runs regardless of PII_ARMED.
# Union, not swap: $(_lib_config_dir)'s copy wins if present, else the legacy $HOME/.claude location -- keeps an already-armed CLAUDE_CONFIG_DIR user's guard live.
# An unresolvable config dir leaves PII_PATTERNS_FILE at the legacy path; this is an opt-in guard, not a gate, so resolver failure must not disable it.
PII_PATTERNS_FILE="${HOME}/.claude/pii-patterns.md"
if config_dir=$(_lib_config_dir) && [ -f "$config_dir/pii-patterns.md" ]; then
  PII_PATTERNS_FILE="$config_dir/pii-patterns.md"
fi
PII_ARMED=0
if [ -f "$PII_PATTERNS_FILE" ] && [ -r "$PII_PATTERNS_FILE" ]; then
  PII_ARMED=1
fi

# --- Parse ~/.claude/pii-patterns.md (armed users only) -------------------
# USER_LABELS[i] / USER_REGEXES[i] are parallel arrays; EXCLUDE_GLOBS holds
# `exclude:` paths. A malformed line denies fail-closed. Declared
# unconditionally (empty) so the scan logic below can reference them regardless of arming.
USER_LABELS=()
USER_REGEXES=()
EXCLUDE_GLOBS=()
if [ "$PII_ARMED" -eq 1 ]; then
while IFS=$'\t' read -r config_lineno line; do
  case "$line" in
    *:*) ;;
    *)
      # shellcheck disable=SC2088 # the leading ~ is prose naming the config
      # file's conventional path for the human reading the deny message, not
      # a shell path this script expands.
      emit_deny "~/.claude/pii-patterns.md line ${config_lineno} is not a valid entry — every non-comment line must be '<label>: <regex>' or 'exclude: <glob>'. A line the hook cannot parse is an unscanned PII pattern, so the gate fails closed. Fix the line and retry."
      exit 0
      ;;
  esac

  config_label=${line%%:*}
  config_value=${line#*:}
  # Trim the label (trailing whitespace) and the value (leading whitespace).
  config_label="${config_label%"${config_label##*[![:space:]]}"}"
  config_value="${config_value#"${config_value%%[![:space:]]*}"}"
  if [ -z "$config_label" ] || [ -z "$config_value" ]; then
    # shellcheck disable=SC2088 # prose path, not a shell expansion — see the
    # identical rationale above.
    emit_deny "~/.claude/pii-patterns.md line ${config_lineno} has an empty label or empty value. Every non-comment line must be '<label>: <regex>' or 'exclude: <glob>'. The gate fails closed on an unparseable pattern line. Fix the line and retry."
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
    # shellcheck disable=SC2088 # prose path, not a shell expansion — see the
    # identical rationale above.
    emit_deny "~/.claude/pii-patterns.md line ${config_lineno} ('${config_label}') has a regex that grep -E cannot compile. The gate fails closed rather than silently skip a PII pattern. Fix the regex and retry."
    exit 0
  fi
  USER_LABELS+=("$config_label")
  USER_REGEXES+=("$config_value")
done < <(_lib_config_lines "$PII_PATTERNS_FILE")
fi

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

# The cap-kill statuses are listed in _lib_capped_for's header in _lib.sh,
# which also says a status alone cannot prove the cap fired. This only picks
# the deny message text ("by the scan cap or a signal"): callers deny on any
# nonzero status either way.
status_is_killed() {
  [ "$1" -eq 124 ] || [ "$1" -eq 137 ] || [ "$1" -eq 143 ]
}

# --- Build the scan target -----------------------------------------------
# Always exclude this hook's own synthetic-PII test fixtures, plus every
# user-configured `exclude:` glob. `:(top,exclude)` is repo-root-relative.
PATHSPEC_EXCLUDES=(':(top,exclude)claude/.claude/hooks/tests/**')
for exclude_glob in "${EXCLUDE_GLOBS[@]:-}"; do
  [ -z "$exclude_glob" ] && continue
  PATHSPEC_EXCLUDES+=(":(top,exclude)${exclude_glob}")
done

# The command string carries the commit message (`-m "..."`). Kept raw
# here. The raw+stripped union (SCAN_TARGET_BOTH, below) is computed once
# all diff and message-file content is appended, since a quote-adjacent
# token's word boundary survives only in the raw copy once quotes are
# stripped -- see _lib_strip_shell_quotes.
SCAN_TARGET="$COMMAND"

added_lines_of() {
  # Keep real `+` content lines, drop `+++` file headers.
  grep -E '^\+' | grep -vE '^\+\+\+' || true
}

# Repo and user config (color.diff, diff.external, textconv drivers) can alter
# diff output at exit 0 so the `^\+` filter above drops a credential line.
# `diff.relative` would also scope the diff to the hook's cwd subdirectory.
# These flags keep the scanned text plain and driver-free, and
# `-c diff.relative=false` keeps it repo-wide.
# `-c` is a git-level option, so it precedes `diff`, and it outranks repo and
# user config. A git that predates the `diff.relative` key ignores it silently.
# The flags follow `--cached` / `HEAD` because the fake-git shims in
# test_deny_pii_in_commits.py match `git -c diff.relative=false diff --cached`
# and `git -c diff.relative=false diff HEAD` by argument position.
DIFF_PLAIN_FLAGS=(--no-color --no-ext-diff --no-textconv)

# Both diff calls run unconditionally (the credential-value scan needs their
# output regardless of arming), so each is wrapped in _lib_capped's 5s timeout
# backstop -- and, per _lib_capped's own calling contract, its exit status is
# checked and failed closed on. A silent truncated/empty diff on timeout would
# scan less than the real diff and could let credential content past the gate.
STAGED_DIFF=$(_lib_capped git -c diff.relative=false diff --cached "${DIFF_PLAIN_FLAGS[@]}" -- "${PATHSPEC_EXCLUDES[@]}" 2>/dev/null)
STAGED_DIFF_STATUS=$?
if status_is_killed "$STAGED_DIFF_STATUS"; then
  emit_deny "Commit — could not compute the staged diff: git diff --cached was killed (exit ${STAGED_DIFF_STATUS}), by the scan cap or a signal. Fail-closed — the guard cannot verify staged content is free of PII/credentials without it."
  exit 0
fi
if [ "$STAGED_DIFF_STATUS" -ne 0 ]; then
  emit_deny "Commit — git diff --cached failed (exit ${STAGED_DIFF_STATUS}). Fail-closed — the guard cannot verify staged content is free of PII/credentials without it."
  exit 0
fi
SCAN_TARGET+=$'\n'"$(printf '%s' "$STAGED_DIFF" | added_lines_of)"

if [ "$HEAD_SCAN_NEEDED" -eq 1 ]; then
  _lib_capped git rev-parse HEAD >/dev/null 2>&1
  HEAD_REV_STATUS=$?
  if [ "$HEAD_REV_STATUS" -ne 0 ] && [ "$HEAD_REV_STATUS" -ne 128 ]; then
    emit_deny "Commit — could not resolve HEAD (exit ${HEAD_REV_STATUS}), and this commit form needs a HEAD-relative scan. Fail-closed."
    exit 0
  fi
  # An unborn HEAD has no commit to diff against, so the HEAD scan is
  # skipped. Accepted residual: worktree-vs-index content of an unborn-HEAD
  # commit is not scanned here.
  if [ "$HEAD_REV_STATUS" -eq 0 ]; then
    HEAD_DIFF=$(_lib_capped git -c diff.relative=false diff HEAD "${DIFF_PLAIN_FLAGS[@]}" -- "${PATHSPEC_EXCLUDES[@]}" 2>/dev/null)
    HEAD_DIFF_STATUS=$?
    if status_is_killed "$HEAD_DIFF_STATUS"; then
      emit_deny "Commit — could not compute the HEAD diff: git diff HEAD was killed (exit ${HEAD_DIFF_STATUS}), by the scan cap or a signal. Fail-closed — the guard cannot verify HEAD-relative content is free of PII/credentials without it."
      exit 0
    fi
    if [ "$HEAD_DIFF_STATUS" -ne 0 ]; then
      emit_deny "Commit — git diff HEAD failed (exit ${HEAD_DIFF_STATUS}). Fail-closed — the guard cannot verify HEAD-relative content is free of PII/credentials without it."
      exit 0
    fi
    SCAN_TARGET+=$'\n'"$(printf '%s' "$HEAD_DIFF" | added_lines_of)"
  fi
fi

COMMIT_MSG_SOURCES=$(extract_commit_message_source_paths "$COMMAND")
if [ -n "$COMMIT_MSG_SOURCES" ]; then
  while IFS= read -r msg_path; do
    [ -z "$msg_path" ] && continue
    if _lib_is_pseudo_file_path "$msg_path"; then
      emit_deny "git commit passes a message-source flag pointing at a pseudo-file path ('${msg_path}'). The gate cannot statically verify what git will read from '-' / '/dev/stdin' / '/dev/fd/*'. Inline the message with -m or use a real on-disk file."
      exit 0
    fi
    if [ ! -f "$msg_path" ] || [ ! -r "$msg_path" ]; then
      emit_deny "git commit references a message-source file at '${msg_path}', but that path is not a readable regular file from the hook. The gate refuses to scan it (fail-closed) — unscanned content is the leak vector this hook guards. Create the file, inline the message with -m, or simplify the path if it contains whitespace."
      exit 0
    fi
    SCAN_TARGET+=$'\n'"$(cat "$msg_path" 2>/dev/null || true)"
  done <<< "$COMMIT_MSG_SOURCES"
fi

# Raw+stripped union that the credential-value and user-pattern scans read instead of raw
# $SCAN_TARGET alone. A quote-adjacent digit run (e.g. `x"4111111111111111"`)
# loses the `\b` word boundary the SSN/credit-card regexes below need once
# quotes are stripped, so only the raw copy still matches it -- same fix
# shape as deny-private-project-refs.sh's SCAN_TARGET_BOTH. Checked and
# fail-closed on a strip failure, matching deny-invisible-commit-content.sh's
# own COMMAND_UNQUOTED computation.
SCAN_TARGET_UNQUOTED=$(_lib_strip_shell_quotes "$SCAN_TARGET")
SCAN_TARGET_UNQUOTED_EXIT=$?
if [ "$SCAN_TARGET_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "Commit — could not quote-strip the scan target (exit ${SCAN_TARGET_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than scanning with degraded quote-split coverage."
  exit 0
fi
SCAN_TARGET_BOTH=$(printf '%s\n%s' "$SCAN_TARGET" "$SCAN_TARGET_UNQUOTED")

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

# Prints $1 with each apostrophe-grouped thousands numeral replaced by a space, unless stripping quotes would join its digits to another digit.
# The substitution runs twice because one global pass skips a numeral whose leading bound character the previous match consumed.
# This expression must stay backreference-free: sed's runtime here is linear in input only without one, and a superlinear runtime reopens the fail-open timeout gap at :154-160.
mask_thousands_numerals() {
  local expr=$'s/(^|[^0-9\'"\\$])([\'"\\$]*)[0-9]{1,3}(\'[0-9]{3})+([\'"\\$]*)([^0-9\'"\\$]|$)/\\1\\2 \\4\\5/g'
  printf '%s' "$1" | sed -E -e "$expr" -e "$expr"
}

# --- Scan -----------------------------------------------------------------
# Each match test reads SCAN_TARGET_BOTH via a here-string, not
# `printf | grep`. `grep -q` exits on the first match; in a pipeline that
# SIGPIPEs the `printf`, and under `set -o pipefail` a large SCAN_TARGET_BOTH
# could then make the pipeline report non-zero — dropping a real match. A
# here-string is not a pipeline, so the test reflects only grep's own exit
# status.
MATCHED_LABELS=()

# Unconditional, armed or not — see "Two independent tiers" in the header comment.
if grep -qE "$_LIB_CREDENTIAL_VALUE_REGEX" <<< "$SCAN_TARGET_BOTH"; then
  MATCHED_LABELS+=("Credential value (API token or private key)")
fi

if [ "$PII_ARMED" -eq 1 ]; then
  # SSN/credit-card raw+stripped union, masked before the strip so a thousands separator cannot join digit groups.
  # The credential-value and user-pattern scans keep reading the unmasked SCAN_TARGET_BOTH.
  SSN_CC_MASKED=$(mask_thousands_numerals "$SCAN_TARGET")
  SSN_CC_MASKED_EXIT=$?
  if [ "$SSN_CC_MASKED_EXIT" -ne 0 ]; then
    emit_deny "Commit — could not mask thousands numerals in the SSN/credit-card scan target (exit ${SSN_CC_MASKED_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than scanning with degraded quote-split coverage."
    exit 0
  fi
  SSN_CC_UNQUOTED=$(_lib_strip_shell_quotes "$SSN_CC_MASKED")
  SSN_CC_UNQUOTED_EXIT=$?
  if [ "$SSN_CC_UNQUOTED_EXIT" -ne 0 ]; then
    emit_deny "Commit — could not quote-strip the SSN/credit-card scan target (exit ${SSN_CC_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than scanning with degraded quote-split coverage."
    exit 0
  fi
  SSN_CC_SCAN_TARGET=$(printf '%s\n%s' "$SCAN_TARGET" "$SSN_CC_UNQUOTED")

  if grep -qE '\b[0-9]{3}-[0-9]{2}-[0-9]{4}\b' <<< "$SSN_CC_SCAN_TARGET"; then
    MATCHED_LABELS+=("US Social Security number")
  fi

  CC_CANDIDATES=$(grep -oE '\b[0-9]{13,19}\b' <<< "$SSN_CC_SCAN_TARGET" | sort -u)
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
    if grep -qE -e "${USER_REGEXES[$i]}" <<< "$SCAN_TARGET_BOTH"; then
      MATCHED_LABELS+=("${USER_LABELS[$i]}")
    fi
  done
fi

if [ "${#MATCHED_LABELS[@]}" -gt 0 ]; then
  LABEL_LIST=$(printf '%s\n' "${MATCHED_LABELS[@]}" | sort -u | tr '\n' ',' | sed 's/,/, /g; s/, $//')
  emit_deny "Commit — the staged diff, commit message, or a referenced commit-message file matches: ${LABEL_LIST}. The matched values are not echoed here — they are PII or a live secret. Remove the offending content before committing. For a legitimate synthetic test fixture, add an 'exclude: <repo-relative-glob>' line to ~/.claude/pii-patterns.md rather than disarming the gate. See docs/security-hardening.md."
  exit 0
fi

exit 0
