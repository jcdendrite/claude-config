#!/bin/bash
# hook-class: gate
# Gate: review-only agents (the eight staff-*/ciso-reviewer personas, the
# non-specialist reviewers skill-fidelity-reviewer and
# comment-discipline-reviewer, plus the harness built-ins Explore/Plan — see
# _lib.sh's _LIB_REVIEW_ONLY_AGENTS) must never mutate the tree they are
# reviewing. Their sole sanctioned
# writes are the findings file (agent-reviews/<agent>-<epoch>-<slug>.md,
# via the Write tool) and anything under /tmp. Wired under both the Bash
# and Edit|Write|MultiEdit PreToolUse matchers in settings.json.
#
# Motivation: a transcript scan found review-only agents mutating the tree
# under review across every persona in the roster — `terraform fmt` (no
# -write=false) rewriting a file then `git checkout --` to self-revert,
# `sed -i` mutation-testing a component then restoring from a /tmp backup,
# a throwaway Write-tool probe test cleaned up with `rm -rf`. The one
# mechanism that actually stopped a reviewer in that data was the existing
# worktree-enforcement hook, and only on cross-repo targets — every
# in-worktree mutation ran unimpeded. This hook closes that gap
# unconditionally (no worktree-enforcement opt-in required): a review-only
# agent's cwd is never a legitimate git-write or in-place-format target.
#
# Fast common-path exit: the hook reads .agent_type before any tool-specific
# work and returns immediately for every non-review-only caller (the main
# session, code-writer, general-purpose, or any agent not in the closed
# set) — this keeps it off the latency budget for the overwhelmingly common
# call. `.agent_type` is a documented PreToolUse field
# (code.claude.com/docs/en/hooks) already consumed by
# nudge-error-mode-analysis.sh / nudge-handoff-near-context-cap.sh.
#
# Grounding — the in-place-edit family splits into two tiers:
#
# Pure code formatters (black, isort, gofmt, prettier, rustfmt, and
# terraform/tofu `fmt`) are denied on ANY invocation, regardless of flags. A
# review-only agent reads diffs; it never reformats the tree under review, so
# no read-only mode is exempted — over-denying a check-mode invocation
# (`rustfmt --check`, `prettier --check`, `terraform fmt -check`) is a
# sanctioned deny, not a missed mutation: a reviewer reads the diff, it does
# not need to run the formatter even to verify. terraform/tofu are gated on
# the `fmt` subcommand so their read-only subcommands (validate, plan) stay
# available. This is the deliberate simplification over per-tool write-flag
# parsing — one uniform rule for the tier that has no read-only reviewer use.
#
# Dual-use tools KEEP write-flag gating, because their bare / read-only forms
# are ordinary review actions and denying them would break real review work:
#   - sed / perl: `-i` (a token starting with `-i`) is the in-place-edit flag;
#     without it both print to stdout — a reviewer runs `sed -n`, `perl -ne`
#     constantly, so only the `-i` form denies.
#   - eslint: writes only with `--fix`; without it, only reports — a reviewer
#     legitimately lints, so only `--fix` denies.
#   - ruff: `ruff format` denies unconditionally (any invocation, like the
#     pure-formatter tier above). Otherwise ruff denies whenever a --fix /
#     --fix-only token is present (both write to disk), independent of a
#     literal `check` subcommand token — so `ruff check --fix`, `ruff check
#     --fix-only`, and bare `ruff --fix` are all caught; `ruff check` alone
#     stays allowed as read-only linting.
#
# Known gaps (what this model does NOT close):
#   - GH-751 is only partly closed: _fragment_raw_write_targets below
#     catches a `cp`/`mv`/`tee`/`>`/`>>` write target when it is the
#     fragment's sole or first command, but not the same target hidden
#     behind a bare `&` background operator in the same fragment — `cp
#     /tmp/scratch.txt src/tracked_file.txt & echo /tmp/x` is allowed
#     today (confirmed live). `_lib_split_fragments` does not split a
#     fragment on a bare `&`, so the word-walk still resolves `cp` as the
#     command word and reads a stale trailing word as the destination.
#     GH-811 tracks the underlying `_lib_split_fragments` limitation this
#     depends on; see _fragment_raw_write_targets's own docstring below for
#     its other residual gaps (relative paths, symlinks, fd-numbered
#     redirects, `&>`, `cp -t DIR`, and `tee -`/`tee -- -file`).
#   - A Bash-created symlink that launders the /tmp exemption
#     (`ln -s src/x /tmp/link`, then a Write to `/tmp/link`) — the
#     file-write arm matches the literal `/tmp/*` path and does not resolve
#     symlinks, so the OS write lands on the tracked file. Bounded, and
#     could in principle be closed by resolving the path (realpath) before
#     the match — but that closure would itself resolve `/tmp` to
#     `/private/tmp` on macOS and false-deny every legitimate reviewer
#     /tmp write (see the macOS `/tmp` note below), so it is deliberately
#     left conceded; the vector also requires a deliberate two-step setup
#     no cooperative reviewer performs by accident.
#   - Combined short-option clusters (`sed -ni`, `perl -pi`) and GNU sed's
#     `--in-place` long form are not matched by the `-i`-prefix check below
#     — only literal `-i`/`-i<suffix>` tokens are, a missed mutation for the
#     dual-use text tools. Separately, every pure formatter's read-only check
#     mode (`rustfmt --check`, `prettier --check`, `terraform fmt -check`,
#     `black --check`, `ruff format --check`) is denied by the unconditional
#     rule above — a false deny on a genuinely read-only invocation, not a
#     missed mutation (see Grounding).
#   - Does not resolve a fragment's effective cwd (unlike
#     require-worktree-for-git-writes.sh's -C/cd threading): a reviewer
#     never has a legitimate git-write or in-place-format target anywhere,
#     so cwd is irrelevant to the verdict here — this is the deliberate
#     lighter primitive per CLAUDE.md's default-suspect-over-powered-
#     primitives guidance.
#   - An agent reached only through an alias, wrapper script, or another
#     level of indirection this fragment-level scan cannot see is
#     undecidable, same class of gap as require-worktree-for-git-writes.sh.
#   - A git-write or in-place-edit reached through a command name quoted
#     directly in the Bash arm's own command text (`'sed' -i file`) IS
#     caught: the Bash arm strips quote characters from $COMMAND before
#     splitting into fragments, so the word scan sees the bare token. A
#     command name reached only through a nested shell boundary this scan
#     never executes (`bash -c "git checkout"`) is still undecidable — same
#     indirection class as the alias/wrapper gap above.
#   - macOS `/tmp` is a symlink to `/private/tmp`. If a future harness build
#     resolves `file_path` to its real path before invoking this hook, a
#     legitimate reviewer write under `/tmp/...` would miss the literal
#     `/tmp/*` exemption and be denied (fail-closed friction, not a safety
#     gap). Not observed on the current harness, which passes the path as
#     written.
#   - The `agent-reviews/*` exemption above is no longer a pure string match:
#     it now shells out to `git check-ignore` (via `_lib_capped`, so a
#     coreutils-less machine runs it uncapped rather than failing) to
#     confirm the path is actually ignored before allowing the write, a
#     capability class this hook didn't have before. Any failure of that
#     check — git absent, the resolved cwd not a repo, the timeout firing —
#     denies rather than allows (fail-closed friction on an unusual
#     environment, not a missed mutation).
#   - The Bash arm's COMMAND_UNQUOTED sed/tr strip failure fails closed: its
#     exit status is checked and denies with an explicit message rather than
#     falling through to this hook's normal "no gated fragment matched"
#     allow path.

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
  emit_deny "Blocked by reviewer-tree-mutation hook: could not source _lib.sh — hook cannot evaluate reviewer discipline safely."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by reviewer-tree-mutation hook: could not parse tool-input JSON. Refusing to evaluate reviewer discipline under malformed input."

# .agent_type is the trust-boundary field the entire gate decision hinges on,
# so read it fail-closed — matching _lib_parse_tool_input_or_deny's handling of
# its own jq read. An unchecked read would leave AGENT_TYPE empty on a jq
# failure (timeout, unstringifiable value) and fall straight through to allow:
# the one fail-OPEN path in a file that denies on every other read failure.
if ! AGENT_TYPE=$(printf '%s\n' "$INPUT" | _lib_jq -r '.agent_type // empty' 2>/dev/null); then
  emit_deny "Blocked by reviewer-tree-mutation hook: could not read .agent_type from the tool payload — refusing to evaluate reviewer discipline under an unreadable trust-boundary field."
  exit 0
fi

# Fast common-path exit, BEFORE any tool-specific work: the main session,
# code-writer, general-purpose, and every agent outside the closed
# review-only set pass through unconditionally regardless of tool or command.
_lib_is_review_only_agent "$AGENT_TYPE" || exit 0

SANCTIONED_ALTERNATIVE="Reviewers are read-only on the tree under review. To verify a claim empirically, copy the file to /tmp and mutate the copy there. The only sanctioned in-tree write is the findings file (agent-reviews/<agent>-<epoch>-<slug>.md, via the Write tool)."

# Local to this hook, not _lib.sh: this -i-prefix matcher is the only
# in-place-edit-family word matcher without a second caller elsewhere.

# True iff $1 contains a whitespace-delimited token STARTING with $2 — for
# flags whose value is attached with no separator (sed/perl's -i[SUFFIX]).
_fragment_has_token_prefix() {
  local fragment="$1" prefix="$2"
  [[ "$fragment" =~ (^|[[:space:]])${prefix} ]]
}

# Prints, one per line, every destination a `cp`/`mv`/`tee` invocation or a
# bare `>`/`>>` shell redirect in $1 writes to.
# Catches the target only when the cp/mv/tee/redirect is the fragment's
# sole or first command — a target hidden behind a bare `&` background
# operator in the same fragment is invisible (GH-811 tracks the underlying
# _lib_split_fragments limitation this depends on; see the header's "Known
# gaps" section).
# Does not resolve relative paths, symlinks, fd-numbered redirects
# (`2>file`), or `&>`.
# Does not resolve a `cp -t DIR` target-directory flag, whose destination
# is not the last positional word this word-walk reads.
# Treats a literal `tee -` target and a post-`--` positional filename
# starting with `-` as flags, so neither is ever emitted as a destination
# (GNU `tee` writes a real file named `-`; narrow production risk, not
# fixed here).
# This is a word-walk over the literal target text, not a full
# shell-write-target parse.
# The caller matches the extracted target by literal prefix, the same
# technique the Write/Edit/MultiEdit arm above uses for FILE_PATH.
#
# Caller contract: quote-blind, same as every other word-walk in this file
# — the caller must pass a fragment already quote-stripped via
# _lib_strip_shell_quotes.
_fragment_raw_write_targets() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local -a words=()
  local word
  for word in $fragment; do
    words+=("$word")
  done
  local n="${#words[@]}"
  local i=0 next
  while [ "$i" -lt "$n" ]; do
    word="${words[$i]}"
    case "$word" in
      '>'|'>>')
        next=$((i + 1))
        [ "$next" -lt "$n" ] && printf '%s\n' "${words[$next]}"
        ;;
      '>>'*)
        printf '%s\n' "${word#>>}"
        ;;
      '>'*)
        printf '%s\n' "${word#>}"
        ;;
    esac
    i=$((i + 1))
  done
  if [ "$n" -gt 0 ] && (_lib_fragment_invokes_tool "$fragment" cp || _lib_fragment_invokes_tool "$fragment" mv); then
    printf '%s\n' "${words[$((n - 1))]}"
  fi
  if [ "$n" -gt 0 ] && _lib_fragment_invokes_tool "$fragment" tee; then
    local seen_tee=false
    i=0
    while [ "$i" -lt "$n" ]; do
      word="${words[$i]}"
      if $seen_tee; then
        case "$word" in
          -*) ;;
          *) printf '%s\n' "$word" ;;
        esac
      fi
      case "$word" in
        tee|*/tee) seen_tee=true ;;
      esac
      i=$((i + 1))
    done
  fi
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
}

case "$TOOL_NAME" in
  Write|Edit|MultiEdit)
    if ! FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null); then
      emit_deny "Blocked by reviewer-tree-mutation hook: could not read .tool_input.file_path for $TOOL_NAME — refusing to evaluate the write target under an unreadable path field."
      exit 0
    fi
    [ -z "$FILE_PATH" ] && exit 0
    case "$FILE_PATH" in
      # Traversal guard FIRST, mirroring require-worktree-for-file-writes.sh:
      # a case glob matches the literal string and does not resolve `..`, so
      # `/tmp/../home/user/repo/src/x` or `agent-reviews/../src/x` would
      # satisfy the `/tmp/*` or `agent-reviews/*` prefix below while actually
      # resolving to a tracked repo file. Reject any path with a `..` segment
      # (leading `../`, embedded `/../`, or trailing `/..`) before the
      # exemptions — a traversal path falls through to the deny, never the
      # exemption. `..` as a bare segment only; a filename like `a..b` is fine.
      ../*|*/../*|*/..) ;;
      /tmp/*) exit 0 ;;
      # Matched as a /-delimited path component (leading "agent-reviews/"
      # or a nested "*/agent-reviews/*"), not a substring — a file literally
      # named agent-reviews-notes.md must not be exempted.
      agent-reviews/*|*/agent-reviews/*)
        if ! CWD=$(printf '%s\n' "$INPUT" | _lib_jq -r '.cwd // empty' 2>/dev/null); then
          emit_deny "Blocked by reviewer-tree-mutation hook: could not read .cwd from the tool payload — refusing to evaluate whether agent-reviews/ is actually ignored under an unreadable trust-boundary field."
          exit 0
        fi
        # macOS's system /bin/bash (3.2, what a bare `#!/bin/bash` shebang
        # resolves to) treats `cd ''` as a silent no-op that stays in the
        # current directory and returns 0 — unlike bash 4+, which errors.
        # Relying on `cd "$CWD"` to fail on an empty CWD would silently check
        # whatever directory the hook process happens to be running in
        # instead of failing closed. Verified empirically on this machine's
        # /bin/bash 3.2.57 and Homebrew bash 5.3.15. Deliberately stricter
        # than require-worktree-for-git-writes.sh:103's `[ -z "$CWD" ] &&
        # CWD="$PWD"` fallback: that hook's invariant is "is this a git
        # write inside a worktree," where an unresolvable cwd can safely
        # default to allow (nothing to enforce). This hook's invariant is
        # "the ignore state is confirmed," where an unresolvable cwd is
        # itself the unconfirmed case — denying is the only fail-closed
        # answer, not an oversight.
        if [ -z "$CWD" ]; then
          emit_deny "Blocked by reviewer-tree-mutation hook: the tool payload carried no .cwd — refusing to check whether agent-reviews/ is ignored without knowing which repo to check."
          exit 0
        fi
        # A findings-file write is only safe when agent-reviews/ is actually
        # ignored in the target repo — a stale worktree-local info/exclude or
        # a repo with no ignore entry at all must not silently let an
        # unignored file through. `unset` first: an inherited GIT_DIR/
        # GIT_WORK_TREE/GIT_INDEX_FILE would redirect the check to a
        # different repo's ignore rules than the one actually being written
        # to (mirrors require-worktree-for-git-writes.sh:100). `cd "$CWD"`
        # rather than `-C` against a separately-resolved repo root puts the
        # check in the same path-resolution frame the Write tool itself uses
        # to resolve a relative FILE_PATH — `git -C <path>` would resolve
        # FILE_PATH against <path>, not against $CWD, and the two diverge
        # whenever $CWD is a subdirectory of the repo root.
        # _lib_capped (not a bare `timeout 5`): on stock macOS without GNU
        # coreutils, a bare `timeout 5 git ...` is "command not found" (127),
        # which would permanently deny every agent-reviews/* write on that
        # machine with a misleading message. _lib_capped runs uncapped
        # instead of failing when `timeout` is absent, so the check still
        # works correctly there — see its own comment in _lib.sh.
        #
        # `cd` is checked on its own line, separately from git's own exit
        # status: `git check-ignore -q` genuinely returns 1 for "not
        # ignored," and a failed `cd` (an unresolvable $CWD) also exits 1 —
        # collapsing them into one exit code would report "not actually
        # ignored" for a $CWD that was never checked at all. Sentinel exit 3
        # marks a cd failure distinctly; git/timeout never produce 3 here
        # (git-check-ignore(1): 0/1/128; _lib_capped's wrapped timeout: 124
        # on expiry, or the wrapped command's own code).
        (
          unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
          cd "$CWD" 2>/dev/null || exit 3
          _lib_capped git check-ignore -q -- "$FILE_PATH" 2>/dev/null
        )
        IGNORE_CHECK_STATUS=$?
        case "$IGNORE_CHECK_STATUS" in
          0) exit 0 ;;
          1)
            emit_deny "Blocked by reviewer-tree-mutation hook: 'agent-reviews/' is not actually ignored in this repo — findings_path dispatch is unsafe here. Fall back to inline output; do not create or modify ignore rules yourself — that is the dispatching skill's job, not the reviewer's."
            exit 0
            ;;
          3)
            emit_deny "Blocked by reviewer-tree-mutation hook: could not confirm 'agent-reviews/' is ignored — the payload's .cwd ('$CWD') does not resolve to a directory this process can enter."
            exit 0
            ;;
          *)
            emit_deny "Blocked by reviewer-tree-mutation hook: could not confirm 'agent-reviews/' is ignored (exit $IGNORE_CHECK_STATUS — not a git repo, or the check failed) — refusing to allow the write under an unconfirmed invariant."
            exit 0
            ;;
        esac
        ;;
    esac
    emit_deny "Blocked by reviewer-tree-mutation hook: $TOOL_NAME targets '$FILE_PATH', which is outside /tmp and outside an agent-reviews/ findings path. $SANCTIONED_ALTERNATIVE"
    exit 0
    ;;
  Bash)
    # Reused from require-worktree-for-git-writes.sh: build the read-only
    # git-subcommand alternation once, from the single source of truth in
    # _lib.sh, rather than re-declaring the list here — but STRICTER. That
    # shared list is calibrated for require-worktree-for-git-writes.sh, whose
    # invariant is "a git write must run inside a worktree" (a WHERE question),
    # so it admits subcommands that only list by default yet write git state
    # with a flag: its own entries tag branch/tag/worktree/symbolic-ref as
    # "acceptable risk under this hook's working-tree-race threat model." This
    # hook's invariant is stricter and different in kind — a review-only agent
    # writes NO git state anywhere — so those mode-dependent subcommands
    # (git branch -D, git tag -d, git worktree remove/prune, git remote set-url,
    # git fetch, git fsck --lost-found, git reflog expire,
    # git symbolic-ref HEAD <ref>) are NOT read-only here. Subtract them: a
    # reviewer may run only git subcommands that cannot write under any flag.
    # This over-denies their bare list/check forms (a plain `git branch`, a
    # plain `git fsck`), the same accepted false-positive the in-place-edit
    # family already makes for `black --check`/`isort --diff` above.
    REVIEWER_GIT_WRITE_CAPABLE=(branch fetch fsck reflog remote symbolic-ref tag worktree)
    ALLOWED_SUBCMDS=()
    while IFS= read -r subcmd; do
      case " ${REVIEWER_GIT_WRITE_CAPABLE[*]} " in
        *" $subcmd "*) continue ;;
      esac
      ALLOWED_SUBCMDS+=("$subcmd")
    done < <(_lib_readonly_git_subcmds)
    ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

    # Quote-stripped so an adjacent-quote split (`'sed' -i file`, `"git"
    # checkout`) can't dodge the word-walk detectors below — same helper
    # as deny-network-installs.sh. Checked and fail-closed, matching
    # deny-invisible-commit-content.sh's own COMMAND_UNQUOTED computation.
    COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
    COMMAND_UNQUOTED_EXIT=$?
    if [ "$COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
      emit_deny "Blocked by reviewer-tree-mutation hook: could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned command for a review-only agent."
      exit 0
    fi

    FRAGMENTS=$(_lib_split_fragments "$COMMAND_UNQUOTED")
    FRAGMENTS_SPLIT_EXIT=$?
    if [ "$FRAGMENTS_SPLIT_EXIT" -ne 0 ]; then
      emit_deny "Blocked by reviewer-tree-mutation hook: could not split the command into fragments (exit ${FRAGMENTS_SPLIT_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned command for a review-only agent."
      exit 0
    fi
    while IFS= read -r fragment; do
      [ -z "$fragment" ] && continue

      if _lib_fragment_invokes_git "$fragment"; then
        subcmd=$(_lib_extract_git_subcmd "$fragment")
        # Unlike require-worktree-for-git-writes.sh, cwd is irrelevant here:
        # a reviewer never has a legitimate git-write target anywhere, so
        # any non-read-only subcommand denies regardless of where it runs.
        if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'git $subcmd' is not a read-only git subcommand for a review-only agent (subcommands that can write git state with a flag — branch, tag, worktree, remote, fetch, fsck, reflog, symbolic-ref — are excluded, so a reviewer gets only subcommands that never write), and review-only agents never write git state anywhere (worktree or main tree). $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
        continue
      fi

      # Closed in-place-edit family. Each tool word is matched the same way
      # _lib_fragment_invokes_git matches "git": an exact word, or a word
      # ending in "/<tool>" (absolute/relative path invocation).
      # Pure code formatters. A review-only agent reads diffs; it never
      # reformats the tree under review, so these deny on ANY invocation —
      # no read-only mode is exempted. This is the deliberate simplification
      # over per-tool write-flag parsing (-w / --write / --check /
      # -write=false): under the cooperative threat model, over-denying a
      # formatter's check mode (e.g. `rustfmt --check`, `prettier --check`)
      # is a clear sanctioned deny, not a mutation escape — a reviewer reads
      # the diff, it does not need to run the formatter even to verify.
      for formatter in black isort gofmt prettier rustfmt; do
        if _lib_fragment_invokes_tool "$fragment" "$formatter"; then
          emit_deny "Blocked by reviewer-tree-mutation hook: '$formatter' reformats files and a review-only agent never reformats the tree under review. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      done

      # terraform / tofu are multi-command tools; only the fmt subcommand
      # writes. Gate on the fmt subcommand so read-only subcommands (validate,
      # plan) stay available, but deny fmt regardless of -check / -write=false
      # — same over-deny-the-check-mode stance as the pure formatters above.
      if _lib_fragment_invokes_tool "$fragment" terraform || _lib_fragment_invokes_tool "$fragment" tofu; then
        if _lib_fragment_has_token "$fragment" fmt; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'terraform/tofu fmt' reformats files and a review-only agent never reformats the tree under review. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      # Linters and text tools KEEP write-flag gating — unlike the pure
      # formatters above, their bare / read-only forms (`ruff check`, `eslint`
      # without --fix, `sed`/`perl` without -i) are ordinary review actions a
      # reviewer legitimately runs, so only the mutating flag/subcommand denies.
      if _lib_fragment_invokes_tool "$fragment" ruff; then
        # Deny `ruff format` (any invocation) and any ruff invocation carrying
        # a --fix or --fix-only token (both write to disk). Matched as exact
        # tokens, NOT a --fix* prefix, so the read-only `--fixable` selector
        # (`ruff check --fixable RULE`, which filters fixable rules without
        # writing) is not false-denied — that would contradict keeping linter
        # reads available. Gated on the flag, NOT on a literal `check` token,
        # so `ruff check --fix-only` and bare `ruff --fix` (implicit-check
        # form) are both caught; `ruff check` alone stays allowed.
        if _lib_fragment_has_token "$fragment" format \
          || _lib_fragment_has_token "$fragment" --fix \
          || _lib_fragment_has_token "$fragment" --fix-only; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'ruff format' / 'ruff --fix' rewrites files in place. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      if _lib_fragment_invokes_tool "$fragment" eslint && _lib_fragment_has_token "$fragment" --fix; then
        emit_deny "Blocked by reviewer-tree-mutation hook: 'eslint --fix' rewrites the file in place. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      if _lib_fragment_invokes_tool "$fragment" sed || _lib_fragment_invokes_tool "$fragment" perl; then
        if _fragment_has_token_prefix "$fragment" -i; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'sed -i'/'perl -i' rewrites the file in place. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      # GH-751: raw-write-target check, mirroring the Write/Edit/MultiEdit
      # arm's own /tmp/* exemption above.
      #   - Base rule: a cp/mv/tee destination or a `>`/`>>` shell redirect
      #     that does not literally start with /tmp/ writes outside the one
      #     sanctioned reviewer scratch location, and denies.
      #   - Does not exempt agent-reviews/*: the ignore-state confirmation
      #     that exemption depends on (git check-ignore, above) has no
      #     Bash-arm counterpart, so a Bash write there denies exactly like
      #     any other non-/tmp target — a stricter, sound default rather
      #     than reproducing an unchecked exemption.
      #   - /dev/null is exempted: it is the common diagnostic-noise
      #     destination (`... > /dev/null 2>&1`), not a tracked-file write.
      while IFS= read -r raw_target; do
        [ -z "$raw_target" ] && continue
        case "$raw_target" in
          /dev/null) continue ;;
          ../*|*/../*|*/..)
            emit_deny "Blocked by reviewer-tree-mutation hook: '$fragment' writes to '$raw_target', a path-traversal write target outside /tmp. $SANCTIONED_ALTERNATIVE"
            exit 0
            ;;
          /tmp/*) continue ;;
        esac
        emit_deny "Blocked by reviewer-tree-mutation hook: '$fragment' writes to '$raw_target', which is outside /tmp. $SANCTIONED_ALTERNATIVE"
        exit 0
      done < <(_fragment_raw_write_targets "$fragment")
    # <<< here-string, not < <(...) process substitution: _lib_split_fragments
    # emits no trailing newline for a single, unsplit fragment, and a
    # process-substitution `read` returns non-zero (loop body never runs) on
    # a final line with no newline delimiter. `$(...)` command substitution
    # strips any trailing newline and `<<<` re-adds exactly one, guaranteeing
    # the last fragment is newline-terminated. Mirrors deny-pii-in-commits.sh
    # and deny-private-project-refs.sh's identical assign-then-`<<<
    # "$VAR"`-here-string pattern.
    done <<< "$FRAGMENTS"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
