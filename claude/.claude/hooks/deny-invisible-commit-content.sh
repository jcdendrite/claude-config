#!/bin/bash
# hook-class: gate
# Gate: denies a Bash `git commit` whose actually-committed content cannot
# be described by the `git diff --cached` snapshot every other commit gate
# reads at PreToolUse time — closing a time-of-check-to-time-of-use gap
# that lets an unreviewed commit land. See docs/hooks.md for the current
# list of commit gates that depend on this one for their own empty-diff
# carve-out to stay sound.
#
# Two independent checks run.
#  - Arm 1 walks the command's git fragments in order over quote-stripped
#    text. It denies:
#    - a non-read-only git subcommand chained ahead of the first commit
#      fragment
#    - that commit fragment carrying `-a`/`--all`
#    - a `--` pathspec separator
#    - a bare pathspec argument
#  - Arm 2 counts git-commit-invoking fragments over quote-masked text and
#    denies more than one anywhere in the command, since arm 1 stops at the
#    first commit fragment and cannot see a second.
# Arm 1's own worktree-target check reuses arm 2's masked fragment for a
# direct (unwrapped) commit invocation rather than its own quote-stripped
# one, so a multi-word quoted `-m` value stays one token for the shared
# tokenizer instead of splitting into several bare-looking trailing words.
#
# Dispatch: wired on the PreToolUse `Bash` matcher with NO `if`-condition,
# so it runs for every Bash tool call and filters internally via the
# fast-reject grep below — matching deny-pii-in-commits.sh's own
# defense-in-depth posture.
#
# Known gaps this gate does not close:
#  - `git commit --amend` with no `-a` and no chained mutation folds
#    HEAD's tree into the commit, content never in `--cached` either.
#    Left open because closing it would break the amend-message-only flow
#    every commit gate's empty-diff carve-out exists to permit.
#  - Quote/indirection obfuscation of the commit detection itself
#    (`g"it commit"`, a shell variable holding the git path, a heredoc
#    body piped to an interpreter) — the same surface every other commit
#    gate already has.
#  - `git -C <other-repo> commit` — the gates hash the session's own repo,
#    not the `-C` target.
#  - A `$(...)`/backtick substitution inside a commit's own arguments
#    (e.g. `git commit -m "$(git add f; echo x)"`) executes before the
#    commit runs and is not inspected. Closing it would deny the standard
#    `git commit -m "$(cat <<'EOF' ... EOF)"` heredoc idiom whenever the
#    message text happens to mention a git command.
#  - Accepted false positive: `_lib_fragment_invokes_git` matches `git` as
#    any word, so `echo "git add ." && git commit` denies even though the
#    `git add` never actually runs. `deny-pii-in-commits.sh`'s identical
#    fragment walk has the same property. This any-word matching is a
#    different defect class from quote-masking correctness. `_mask_shell_
#    quotes` correctly tracks which quote type is currently open. A literal
#    quote character of the other kind inside a real argument does not
#    affect the fragment count, and neither does an argument spanning
#    multiple physical lines.
#  - Wrapped-invocation blind spot: `_mask_shell_quotes` collapses an
#    entire quoted span to nothing, so a real `git commit` invoked inside a
#    code-executing wrapper's quoted argument (`bash -c "git commit ..."`,
#    `eval "git commit ..."`, and similar) is invisible to arm 2's count.
#    A two-commit chain where either commit is wrapped this way is not
#    detected by either arm — e.g. `git commit -m "fix" && bash -c "git
#    add secret && git commit -m y"`. Accepted under this repo's
#    cooperative-agent threat model (see require-respond-pr.sh's own
#    "Threat model" comment for the same posture stated elsewhere): these
#    hooks assume a cooperative agent, not one deliberately constructing
#    shell indirection to evade a gate.
#  - Quote-embedded decoy fragment: arm 1's ordered walk classifies
#    fragments over quote-stripped text, so a quoted argument to an
#    unrelated command that happens to contain the literal text "git
#    commit" becomes an indistinguishable fake commit fragment after
#    stripping — e.g. `echo "foo && git commit" && git add secret && git
#    commit -m x`. If it's the first commit-shaped fragment the walk
#    reaches, the walk stops there and never inspects a real, later
#    mutation-then-commit sequence. Accepted for the same cooperative-agent
#    reason as the wrapped-invocation gap above.
#  - None of this hook's own forks carries an internal timeout. Per the
#    harness's PreToolUse contract (code.claude.com/docs/en/hooks, fetched
#    2026-09-01), a timed-out command-type hook like this one is canceled
#    with its output discarded. The tool call then proceeds through normal
#    permission flow rather than being blocked. A wedged or replaced
#    grep/sed/tr/awk/xargs binary is therefore not bounded by any timeout
#    here — a timeout just means this gate silently did not run. This gap
#    predates this hook and is shared by every sibling always-on commit
#    gate.
#
# Subprocess footprint once the fast-reject grep matches: pure string-
# processing forks (grep/sed/tr/awk/xargs), no filesystem or network
# access, so none needs the `_lib_capped`/`timeout` wrapping `_lib_jq`
# gets. Every fork's exit status is checked and fails closed on a
# non-zero result, matching `_lib_parse_tool_input_or_deny`'s jq
# discipline. `_mask_shell_quotes`'s per-character awk scan is O(n²) on
# command length (empirically ~12s at 500KB input on this machine's
# /usr/bin/awk), so an unusually large single Bash command's masking cost
# grows faster than linearly — accepted because reaching the harness's
# PreToolUse timeout at this scaling would require a multi-megabyte single
# command, well outside normal usage.
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
  emit_deny "Blocked by invisible-commit-content gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by invisible-commit-content gate: could not parse tool-input JSON."

# Only gate Bash tool calls — exit 0 (no opinion) for everything else.
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Fast-reject: only continue for commands that mention `git commit` in some
# textual form. Copied verbatim from require-code-review.sh so every
# commit gate shares one detection shape. Matches `git commit` at the
# start of the command OR after a shell separator (&&, ||, ;, |), so
# chained forms like `git add . && git commit` are also caught. The
# trailing (\s|$) avoids matching `git commit-tree` or similar.
printf '%s\n' "$COMMAND" | grep -qE '(^|&&?|;|\|\|?)\s*git\s+commit(\s|$)'
FAST_REJECT_EXIT=$?
if [ "$FAST_REJECT_EXIT" -eq 1 ]; then
  exit 0
fi
if [ "$FAST_REJECT_EXIT" -ne 0 ]; then
  emit_deny "Blocked by invisible-commit-content gate: could not evaluate the fast-reject grep (exit ${FAST_REJECT_EXIT}) — grep may be missing, killed, or errored. Failing closed rather than silently allowing an unscanned git commit."
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
# 1's `_lib_strip_shell_quotes`.
_mask_shell_quotes() {
  printf '%s' "$1" | awk -v dq='"' -v sq="'" '
    BEGIN { RS = "\0" }
    {
      n = length($0)
      quote = ""
      quote_start = 0
      result = ""
      for (i = 1; i <= n; i++) {
        c = substr($0, i, 1)
        if (quote == "") {
          if (c == dq || c == sq) {
            quote = c
            quote_start = i
          } else {
            result = result c
          }
        } else if (c == quote) {
          result = result quote c
          quote = ""
        }
      }
      if (quote != "") {
        result = result substr($0, quote_start)
      }
      printf "%s", result
    }
  '
}

# ------------------------------------------------------------------ #
# Arm 2: deny a chain carrying more than one git-commit-invoking       #
# fragment. Independent of arm 1 below, and must run first — arm 1's   #
# ordered walk exits unconditionally at the first commit fragment it   #
# finds, so it never reaches a second one in the same chain.           #
# ------------------------------------------------------------------ #
MASKED_COMMAND=$(_mask_shell_quotes "$COMMAND")
MASK_EXIT=$?
if [ "$MASK_EXIT" -ne 0 ]; then
  emit_deny "Blocked by invisible-commit-content gate: could not mask quoted command text (exit ${MASK_EXIT}) — awk may be missing, killed, or errored. Failing closed rather than allowing an unscanned git commit chain."
  exit 0
fi

MASKED_FRAGMENTS=$(_lib_split_fragments "$MASKED_COMMAND")
MASKED_SPLIT_EXIT=$?
if [ "$MASKED_SPLIT_EXIT" -ne 0 ]; then
  emit_deny "Blocked by invisible-commit-content gate: could not split the masked command into fragments (exit ${MASKED_SPLIT_EXIT}). Failing closed rather than allowing an unscanned git commit chain."
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
  if [ "$(_lib_extract_git_subcmd "$masked_fragment")" = "commit" ]; then
    COMMIT_FRAGMENT_COUNT=$((COMMIT_FRAGMENT_COUNT + 1))
    if _lib_fragment_invokes_tool "$masked_fragment" git; then
      DIRECT_MASKED_COMMIT_FRAGMENT="$masked_fragment"
    fi
  fi
done <<< "$MASKED_FRAGMENTS"

if [ "$COMMIT_FRAGMENT_COUNT" -gt 1 ]; then
  emit_deny "Commit blocked by invisible-commit-content gate: this Bash call chains ${COMMIT_FRAGMENT_COUNT} git commit invocations together, but every commit gate evaluates \`git diff --cached\` once per Bash tool call — any commit after the first runs against a snapshot no gate re-checked. Each git commit must run as its own, separate Bash tool call."
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
STRIPPED_COMMAND=$(_lib_strip_shell_quotes "$COMMAND")
STRIP_EXIT=$?
if [ "$STRIP_EXIT" -ne 0 ]; then
  emit_deny "Blocked by invisible-commit-content gate: could not quote-strip the command text (exit ${STRIP_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned git commit."
  exit 0
fi

# Built once, from the single source of truth in _lib.sh, mirroring
# deny-reviewer-tree-mutation.sh's and require-worktree-for-git-writes.sh's
# own read-only-subcommand alternation pattern.
ALLOWED_SUBCMDS=()
while IFS= read -r subcmd; do
  ALLOWED_SUBCMDS+=("$subcmd")
done < <(_lib_readonly_git_subcmds)
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

STRIPPED_FRAGMENTS=$(_lib_split_fragments "$STRIPPED_COMMAND")
SPLIT_EXIT=$?
if [ "$SPLIT_EXIT" -ne 0 ]; then
  emit_deny "Blocked by invisible-commit-content gate: could not split the command into fragments (exit ${SPLIT_EXIT}). Failing closed rather than allowing an unscanned git commit."
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
      emit_deny "Commit blocked by invisible-commit-content gate: this git commit uses -a/--all, a -- pathspec separator, or a bare pathspec argument, which commits working-tree content that was not in the index when every commit gate's \`git diff --cached\` snapshot ran — that content was never reviewed. Stage the changes explicitly first (git add), then commit with no -a/--all and no pathspec."
      exit 0
    fi
    exit 0
  fi
  if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
    # _lib_split_fragments preserves each fragment's surrounding whitespace
    # (e.g. a trailing space before the `&&` it was split on); trim it so
    # the quoted fragment in the deny message below reads cleanly.
    trimmed_fragment="${fragment#"${fragment%%[![:space:]]*}"}"
    trimmed_fragment="${trimmed_fragment%"${trimmed_fragment##*[![:space:]]}"}"
    emit_deny "Commit blocked by invisible-commit-content gate: '${trimmed_fragment}' runs 'git ${subcmd:-<subcommand>}' before this call's git commit and can change what ends up staged, but every commit gate reads \`git diff --cached\` before this Bash call executes — so whatever this fragment stages, unstages, or otherwise mutates is invisible to those gates. Staging must run as its own Bash tool call, with git commit as a second, separate call."
    exit 0
  fi
done <<< "$STRIPPED_FRAGMENTS"

exit 0
