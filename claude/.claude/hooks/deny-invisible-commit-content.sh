#!/bin/bash
# hook-class: gate
# Gate: denies a Bash `git commit` whose actually-committed content cannot
# be described by the `git diff --cached` snapshot every other commit gate
# reads at PreToolUse time — closing a time-of-check-to-time-of-use gap
# that lets an unreviewed commit land. See docs/hooks.md for the current
# list of commit gates that depend on this one for their own empty-diff
# carve-out to stay sound, for the shapes this gate closes.
#
# Three independent checks run.
#  - A wrapper/commit co-occurrence pre-check denies outright when the
#    command's raw text carries both a git-commit-shaped fragment (the
#    fast-reject check below) and an execution-wrapper token (`bash -c`,
#    `sh -c`, `eval`, `xargs`, `source`/bare `.`, `perl -e`, `python -c`,
#    `ruby -e`, `node -e`, or similar) anywhere in the same call,
#    independent of order or quote style — closing the wrapped-invocation
#    bypass structurally, without parsing what runs inside the wrapper. See
#    Known gaps below for two still-open cases where the wrapper's own call
#    syntax defeats the word-boundary matching this check and the
#    fast-reject both depend on: a quote delimiter glued directly against
#    the commit text with no separating whitespace, and an unquoted
#    parameter expansion standing in for a required whitespace character.
#    Accepted over-deny cost:
#    - a commit message merely mentioning one of these tokens as ordinary
#      text (e.g. documenting `xargs` usage), since the check does not
#      parse quoting.
#    - a hyphen-joined compound word that merely contains a token as a
#      substring (e.g. "re-source", "co-eval"), since the token regex's
#      non-word-character boundary class treats a hyphen as a legitimate
#      word boundary.
#  - Arm 1 walks the command's git fragments in order over quote-stripped
#    text. It denies:
#    - a non-read-only git subcommand chained ahead of the first commit
#      fragment
#    - that commit fragment carrying `-a`/`--all`
#    - a `--` pathspec separator
#    - a bare pathspec argument
#  - Arm 2 counts git-commit-invoking fragments over quote-masked text and
#    denies more than one anywhere in the command, since arm 1 stops at the
#    first commit fragment and cannot see a second. The same ordered walk
#    over masked fragments also denies a non-read-only git subcommand
#    reached before the first masked commit fragment: masking correctly
#    erases a quote-embedded decoy's content, so a real mutation hidden
#    behind a fake stripped commit fragment in arm 1's quote-stripped walk
#    is still visible here.
# Arm 1's own worktree-target check reuses arm 2's masked fragment for a
# direct (unwrapped) commit invocation rather than its own quote-stripped
# one, so a multi-word quoted `-m` value stays one token for the shared
# tokenizer instead of splitting into several bare-looking trailing words.
#
# Dispatch: wired on the PreToolUse `Bash` matcher with NO `if`-condition,
# so it runs for every Bash tool call and filters internally via the
# fast-reject check below — matching deny-pii-in-commits.sh's own
# defense-in-depth posture.
#
# Known gaps this gate does not close:
#  - `git commit --amend` with no `-a` and no chained mutation folds
#    HEAD's tree into the commit, content never in `--cached` either.
#    Left open because closing it would break the amend-message-only flow
#    every commit gate's empty-diff carve-out exists to permit.
#  - Whole-word quote obfuscation (`"git" commit`) is closed for both arms
#    (see docs/security-hardening.md for the mechanism); a shell variable,
#    heredoc, or mid-word quote split (`g"it commit"`) remains open.
#  - `git -C <other-repo> commit` is not a gap here: this hook never hashes
#    a `git diff --cached` of its own, so a `-C`-qualified commit is
#    evaluated by arm 1/arm 2/the fast-reject exactly like any other — the
#    cross-repo-target-hash concern belongs to hooks that do hash a diff,
#    already documented in `deny-pii-in-commits.sh`'s own header.
#  - A `$(...)`/backtick substitution inside a commit's own arguments
#    (e.g. `git commit -m "$(git add f; echo x)"`) executes before the
#    commit runs and is not inspected. Closing it would deny the standard
#    `git commit -m "$(cat <<'INNER_EOF' ... INNER_EOF)"` heredoc idiom
#    whenever the message text happens to mention a git command.
#  - Accepted false positive: `_lib_fragment_invokes_git` matches `git` as
#    any word, so `echo "git add ." && git commit` denies even though the
#    `git add` never actually runs. `deny-pii-in-commits.sh`'s identical
#    fragment walk has the same property.
#  - The ANSI-C multi-character-escape residual (`$'\x67it'`) contains a
#    backslash, so it fails the masker's single-safe-word exception and
#    stays blanked, invisible to arm 2's fragment count — see
#    docs/security-hardening.md for the mechanism.
#  - An execution mechanism outside the wrapper/commit co-occurrence
#    check's enumerated token list — an `awk` `system()` call, a `make`
#    recipe, `find -exec`, a remote `ssh host <cmd>` — still hides a real
#    `git commit` from every check here. See require-respond-pr.sh's own
#    "Threat model" comment for why this is accepted.
#  - A commit fragment embedded as a wrapper-language string literal with
#    no whitespace between the wrapper's own call syntax and the opening
#    quote evades the fast-reject's bare-word `git` match entirely — e.g.
#    `perl -e "system('git commit -m y')"`, where quote-stripping collapses
#    `system('git` into the single glued word `system(git`, so the
#    wrapper/commit co-occurrence check downstream of the fast-reject never
#    runs either. A piped or interpreter-fed commit fragment with ordinary
#    word spacing (e.g. `printf '%s' 'git commit -m y' | xargs -0 sh -c`)
#    is caught instead, since the fast-reject's word-walk has no chain-
#    position requirement and matches `git` immediately followed by
#    `commit` anywhere a quote-stripped fragment carries them as separate
#    words.
#  - An unquoted parameter expansion standing in for a required whitespace
#    character (e.g. `${IFS}`, `$IFS`) defeats the wrapper-token regex and
#    `_lib_fragment_invokes_git`'s word-split alike, since neither performs
#    real shell tokenization — e.g.
#    `bash${IFS}-c${IFS}"git add secret && git commit -m y"`, where quote-
#    stripping yields the single glued word `bash${IFS}-c${IFS}git`, which
#    neither the wrapper regex's `\s+-c` alternations nor
#    `_lib_fragment_invokes_git` ever recognizes as invoking `bash`/`git`.
#    Every other enumerated wrapper token is defeated the same way.
#  - None of this hook's own forks carries an internal timeout, except
#    `_mask_shell_quotes`'s own 5s `_lib_capped_for` cap (see below) — a
#    wedged or replaced grep/sed/tr/awk/xargs binary is unbounded, so it
#    just means this gate silently did not run (per the harness's
#    PreToolUse contract, code.claude.com/docs/en/hooks, fetched
#    2026-09-01).
#  - Hoisting COMMAND_UNQUOTED's computation ahead of the fast-reject
#    widened this hook's failure blast radius from commit-shaped Bash
#    calls only to every Bash call in the session on a missing or wedged
#    sed/tr.
#
# Subprocess footprint:
#  - The quote-strip (sed+tr) that produces COMMAND_UNQUOTED, and the
#    fast-reject check's own internal quote-strip and fragment-split
#    (GH-783's _lib_command_invokes_git_subcmd), fork unconditionally on
#    every Bash call. Everything past the fast-reject (the wrapper
#    pre-check, arm 1, and arm 2) forks only once the fast-reject matches.
#  - COMMAND_UNQUOTED and the fast-reject's own internal quote-strip
#    independently strip the same raw $COMMAND, an accepted redundant-fork
#    cost: _lib_command_invokes_git_subcmd takes only (COMMAND, SUBCMD),
#    with no pre-stripped-input parameter, the same call-site contract
#    every one of GH-783's eight gate hooks shares (see _lib.sh's own
#    comment above that function), so the two strips cannot be threaded
#    together here.
#  - Every fork here is a pure string-processing one (grep/sed/tr/awk/
#    xargs), with no filesystem or network access.
#  - Every fork's exit status is checked and fails closed on a non-zero
#    result, matching `_lib_parse_tool_input_or_deny`'s jq discipline.
#  - The `_mask_shell_quotes` per-character awk scan is O(n²) on command
#    length, so it runs under the same 5s `_lib_capped_for` cap
#    `_lib_jq`/`_lib_capped` use elsewhere (every other fork in this file
#    stays unbounded). A pathological input denies fast instead of
#    stalling the gate.
#
# Fail-closed on unparseable hook input.

set -uo pipefail

DENY_GATE_LABEL="invisible-commit-content"

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

# Only gate Bash tool calls — exit 0 (no opinion) for everything else.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Quote-stripped so an adjacent-quote split (`"git" commit`) can't dodge
# arm 1's fragment walk further down — same helper as
# deny-network-installs.sh. The fast-reject check below does its own
# internal quote-stripping and does not read this variable. Checked and
# fail-closed, matching every other fork in this hook.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
COMMAND_UNQUOTED_EXIT=$?
if [ "$COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned git commit."
  exit 0
fi

# Fast-reject: only continue for commands that mention `git commit` in some
# textual form. Shares the _lib_command_invokes_git_subcmd matcher every
# other commit gate uses (GH-783), rather than a hand-copied regex.
# The helper does its own quote-stripping internally, so this call passes
# the raw $COMMAND, not the already-stripped COMMAND_UNQUOTED above (which
# arm 1's fragment walk still needs further down).
_lib_command_invokes_git_subcmd "$COMMAND" commit
FAST_REJECT_EXIT=$?
if [ "$FAST_REJECT_EXIT" -eq 1 ]; then
  exit 0
fi
if [ "$FAST_REJECT_EXIT" -ne 0 ]; then
  emit_deny "could not determine whether this command invokes git commit (status ${FAST_REJECT_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than silently allowing an unscanned git commit."
  exit 0
fi

# ------------------------------------------------------------------ #
# Wrapper/commit co-occurrence pre-check: a git-commit-shaped fragment #
# is already confirmed by the fast-reject check above. Denying outright #
# whenever an execution-wrapper token also appears anywhere in the raw #
# command text — independent of order or quote style — forces the      #
# sanctioned split (staging as its own Bash call, commit as a second)  #
# instead of trying to parse what actually runs inside the wrapper. An #
# unquoted parameter expansion standing in for whitespace (`${IFS}`)   #
# defeats this regex — see Known gaps below.                          #
# ------------------------------------------------------------------ #
EXECUTION_WRAPPER_TOKEN_RE='(^|/|\s)(bash|sh|zsh|ksh|dash)\s+-c(\s|$)|(^|[^A-Za-z0-9_])eval([^A-Za-z0-9_]|$)|(^|[^A-Za-z0-9_])xargs([^A-Za-z0-9_]|$)|(^|[^A-Za-z0-9_])source([^A-Za-z0-9_]|$)|(^|&&?|;|\|\|?)\s*\.\s+\S|(^|/|\s)perl\s+-e(\s|$)|(^|/|\s)(python|python2|python3)\s+-c(\s|$)|(^|/|\s)ruby\s+-e(\s|$)|(^|/|\s)node\s+-e(\s|$)'
printf '%s\n' "$COMMAND" | grep -qE "$EXECUTION_WRAPPER_TOKEN_RE"
WRAPPER_TOKEN_EXIT=$?
if [ "$WRAPPER_TOKEN_EXIT" -eq 0 ]; then
  emit_deny "Commit blocked by invisible-commit-content gate: this Bash call carries both a git-commit-shaped fragment and an execution-wrapper token (bash -c, sh -c, eval, xargs, source/bare '.', perl -e, python -c, ruby -e, node -e, or similar) — content executed inside the wrapper is invisible to this and every other commit gate's git diff --cached snapshot, regardless of quoting or ordering. Staging must run as its own Bash tool call, with git commit as a second, separate call."
  exit 0
fi
if [ "$WRAPPER_TOKEN_EXIT" -ne 1 ]; then
  emit_deny "Blocked by invisible-commit-content gate: could not evaluate the execution-wrapper co-occurrence grep (exit ${WRAPPER_TOKEN_EXIT}) — grep may be missing, killed, or errored. Failing closed rather than silently allowing an unscanned git commit."
  exit 0
fi

# Local to this hook, not _lib.sh: single-consumer masking, below the
# two-consumer promotion threshold CLAUDE.md sets for a shared helper.
# Masks each quoted span's interior while leaving its own delimiter pair
# intact (e.g. `"..."` becomes `""`), so a commit message that merely
# mentions "git commit" as literal text is not miscounted as a real
# invocation by arm 2 below. A quote left open at end of string is left
# unmasked, erring toward denying rather than silently swallowing a real
# second commit fragment. See docs/hooks.md's entry for this hook for the
# single-pass quote-state-tracking design and how it contrasts with arm
# 1's `_lib_strip_shell_quotes`. Runs under a 5s `_lib_capped_for` cap —
# the per-character scan is O(n²) on command length.
#
# Exception:
# - A quoted span whose entire interior is a single safe word
#   (`^[A-Za-z0-9._/-]+$`) is emitted unquoted, not blanked, so a quoted
#   `git`/`commit` word (`"git" commit`) survives quoting and stays
#   visible to arm 2's fragment-count loop below.
# - A leading `$` immediately before the opening delimiter is dropped too,
#   mirroring `_lib_strip_shell_quotes`'s own `$'`/`$"` opener rule
#   (`_lib.sh`).
# - Every other quoted span — multi-word, containing whitespace or an
#   operator — still blanks to its delimiter pair unchanged, so
#   `-m "fix && git commit"` still masks the operator inside the message.
_mask_shell_quotes() {
  # False positive: shellcheck's no-warn heuristic for a bare `awk '...'`
  # pipe stage doesn't recognize awk once it's preceded by the
  # _lib_capped_for wrapper; the single-quoted script below is an awk
  # program, not a shell string, and is not meant to expand.
  # shellcheck disable=SC2016
  printf '%s' "$1" | _lib_capped_for 5 awk -v dq='"' -v sq="'" '
    BEGIN { RS = "\0" }
    {
      n = length($0)
      quote = ""
      quote_start = 0
      quote_dollar_prefix = 0
      span = ""
      result = ""
      for (i = 1; i <= n; i++) {
        c = substr($0, i, 1)
        if (quote == "") {
          if (c == dq || c == sq) {
            quote = c
            quote_start = i
            span = ""
            quote_dollar_prefix = (length(result) > 0 && substr(result, length(result), 1) == "$")
          } else {
            result = result c
          }
        } else if (c == quote) {
          if (quote_dollar_prefix) {
            result = substr(result, 1, length(result) - 1)
          }
          if (span ~ /^[A-Za-z0-9._\/-]+$/) {
            result = result span
          } else {
            result = result quote c
          }
          quote = ""
        } else {
          span = span c
        }
      }
      if (quote != "") {
        result = result substr($0, quote_start)
      }
      printf "%s", result
    }
  '
}

# Built once, from the single source of truth in _lib.sh, mirroring
# deny-reviewer-tree-mutation.sh's and require-worktree-for-git-writes.sh's
# own read-only-subcommand alternation pattern. Built ahead of arm 2 below
# — its masked-fragment ordered walk needs the same allowlist arm 1 does.
ALLOWED_SUBCMDS=()
while IFS= read -r subcmd; do
  ALLOWED_SUBCMDS+=("$subcmd")
done < <(_lib_readonly_git_subcmds)
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

# Trims a fragment's surrounding whitespace so a deny message reads
# cleanly. `_lib_split_fragments` preserves each fragment's surrounding
# whitespace (e.g. a trailing space before the `&&` it was split on).
# Shared by arm 1 and arm 2's ordered-mutation check below.
_trim_fragment() {
  local f="$1"
  f="${f#"${f%%[![:space:]]*}"}"
  f="${f%"${f##*[![:space:]]}"}"
  printf '%s' "$f"
}

# ------------------------------------------------------------------ #
# Arm 2: deny a chain carrying more than one git-commit-invoking       #
# fragment, and deny a non-read-only git subcommand reached before the #
# first masked commit fragment. Independent of arm 1 below, and must   #
# run first: arm 1's ordered walk exits unconditionally at the first   #
# commit fragment it finds, so a decoy commit fragment quoted ahead of #
# a real mutation is invisible to it. Masking erases the decoy's       #
# content instead, so this ordered walk over masked text still reaches #
# the real mutation.                                                   #
# ------------------------------------------------------------------ #
MASKED_COMMAND=$(_mask_shell_quotes "$COMMAND")
MASK_EXIT=$?
if [ "$MASK_EXIT" -ne 0 ]; then
  emit_deny "Blocked by invisible-commit-content gate: could not mask quoted command text within the timeout (exit ${MASK_EXIT}) — awk may be missing, killed, have timed out on an unusually large command, or errored. Failing closed rather than allowing an unscanned git commit chain."
  exit 0
fi

MASKED_FRAGMENTS=$(_lib_split_fragments "$MASKED_COMMAND")
MASKED_SPLIT_EXIT=$?
if [ "$MASKED_SPLIT_EXIT" -ne 0 ]; then
  emit_deny "could not split the masked command into fragments (exit ${MASKED_SPLIT_EXIT}). Failing closed rather than allowing an unscanned git commit chain."
  exit 0
fi

COMMIT_FRAGMENT_COUNT=0
# Captured alongside the count above for arm 1's worktree-target check to
# reuse below — see the header comment above for why a masked fragment's
# real quoting matters there. Only ever set from a fragment that is itself
# a direct `git ...` invocation (never `bash -c "..."`/`eval ...`), so a
# wrapped commit's own flags are never silently swapped out for an
# unrelated, later direct commit's clean ones.
DIRECT_MASKED_COMMIT_FRAGMENT=""
while IFS= read -r masked_fragment; do
  [ -z "$masked_fragment" ] && continue
  _lib_fragment_invokes_git "$masked_fragment" || continue
  masked_subcmd=$(_lib_extract_git_subcmd "$masked_fragment")
  if [ "$masked_subcmd" = "commit" ]; then
    COMMIT_FRAGMENT_COUNT=$((COMMIT_FRAGMENT_COUNT + 1))
    if _lib_fragment_invokes_tool "$masked_fragment" git; then
      DIRECT_MASKED_COMMIT_FRAGMENT="$masked_fragment"
    fi
    continue
  fi
  if [ "$COMMIT_FRAGMENT_COUNT" -eq 0 ] && ! [[ "$masked_subcmd" =~ ^($ALLOWED_RE)$ ]]; then
    trimmed_masked_fragment=$(_trim_fragment "$masked_fragment")
    emit_deny "Commit blocked by invisible-commit-content gate: '${trimmed_masked_fragment}' runs 'git ${masked_subcmd:-<subcommand>}' before this call's first real git commit and can change what ends up staged. Every commit gate reads \`git diff --cached\` before this Bash call executes, so whatever this fragment stages, unstages, or otherwise mutates is invisible to those gates — even when a quote-embedded decoy commit earlier in the command hides this fragment from a quote-stripped scan. Staging must run as its own Bash tool call, with git commit as a second, separate call."
    exit 0
  fi
done <<< "$MASKED_FRAGMENTS"

if [ "$COMMIT_FRAGMENT_COUNT" -gt 1 ]; then
  emit_deny "Commit — this Bash call chains ${COMMIT_FRAGMENT_COUNT} git commit invocations together, but every commit gate evaluates \`git diff --cached\` once per Bash tool call — any commit after the first runs against a snapshot no gate re-checked. Each git commit must run as its own, separate Bash tool call."
  exit 0
fi

# ------------------------------------------------------------------ #
# Arm 1: walk the command's shell fragments **in order**, over the     #
# quote-stripped text rather than raw $COMMAND. Quote-stripping is      #
# required to recognize a git invocation whose leading token is glued  #
# to an adjacent quote character (e.g. `bash -c "git add x && git      #
# commit"`, where the raw text's first fragment word is `"git`, not     #
# `git`, and so does not match _lib_fragment_invokes_git's bare-word   #
# test). Order-sensitivity is load-bearing, not incidental: a `git add` #
# fragment *after* the commit fragment is harmless (it stages content  #
# this commit never sees), and a quote-stripped commit message         #
# containing `&&` can synthesize exactly that trailing shape — so the  #
# walk stops at the first commit fragment rather than scanning the     #
# whole command.                                                       #
# ------------------------------------------------------------------ #

STRIPPED_FRAGMENTS=$(_lib_split_fragments "$COMMAND_UNQUOTED")
SPLIT_EXIT=$?
if [ "$SPLIT_EXIT" -ne 0 ]; then
  emit_deny "could not split the command into fragments (exit ${SPLIT_EXIT}). Failing closed rather than allowing an unscanned git commit."
  exit 0
fi

while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue
  _lib_fragment_invokes_git "$fragment" || continue
  subcmd=$(_lib_extract_git_subcmd "$fragment")
  if [ "$subcmd" = "commit" ]; then
    # Prefer arm 2's masked counterpart when this stripped fragment is
    # itself a direct `git ...` invocation — see the header comment above
    # for why. Falls back to the stripped fragment unchanged for a wrapped
    # invocation (`bash -c "git commit ..."`), where the masked
    # counterpart may belong to a different, later invocation entirely.
    commit_check_fragment="$fragment"
    if [ -n "$DIRECT_MASKED_COMMIT_FRAGMENT" ] && _lib_fragment_invokes_tool "$fragment" git; then
      commit_check_fragment="$DIRECT_MASKED_COMMIT_FRAGMENT"
    fi
    if _lib_commit_fragment_has_worktree_target "$commit_check_fragment"; then
      emit_deny "Commit — this git commit uses -a/--all, a -- pathspec separator, or a bare pathspec argument, which commits working-tree content that was not in the index when every commit gate's \`git diff --cached\` snapshot ran — that content was never reviewed. Stage the changes explicitly first (git add), then commit with no -a/--all and no pathspec."
      exit 0
    fi
    exit 0
  fi
  if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
    trimmed_fragment=$(_trim_fragment "$fragment")
    emit_deny "Commit blocked by invisible-commit-content gate: '${trimmed_fragment}' runs 'git ${subcmd:-<subcommand>}' before this call's git commit and can change what ends up staged, but every commit gate reads \`git diff --cached\` before this Bash call executes — so whatever this fragment stages, unstages, or otherwise mutates is invisible to those gates. Staging must run as its own Bash tool call, with git commit as a second, separate call."
    exit 0
  fi
done <<< "$STRIPPED_FRAGMENTS"

exit 0
