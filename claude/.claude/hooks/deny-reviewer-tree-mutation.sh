#!/bin/bash
# hook-class: gate
# Gate: review-only agents (the eight staff-*/ciso-reviewer personas, the
# skill-fidelity-reviewer, plus the harness built-ins Explore/Plan — see
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
# Grounding — each in-place-edit family entry below is denied because the
# tool's OWN documented flag is what makes an invocation read-only; absent
# that flag, the tool writes:
#   - terraform fmt / tofu fmt: writes by default. `-check` ("don't write
#     to files; return nonzero if input would change") and `-write=false`
#     ("don't write to source files") are Terraform/OpenTofu's own
#     read-only flags — exempted.
#   - sed -i / perl -i: `-i`/`--in-place` (a token starting with `-i`) is
#     the in-place-edit flag for both; without it, both print to stdout.
#   - gofmt: writes only with `-w` ("write result to source file instead
#     of stdout"); without it, prints to stdout.
#   - prettier: writes only with `--write`; without it, prints to stdout.
#   - eslint: writes only with `--fix`; without it, only reports.
#   - ruff format: writes by default (no --check exemption is granted here
#     — see Known gaps). ruff check: read-only unless `--fix` is present.
#   - black / isort: write by default; no --check/--diff exemption is
#     granted here (see Known gaps).
#   - rustfmt: writes by default. `--check` ("run in 'check' mode... exits
#     with 1 and prints a diff if formatting is required") is rustfmt's own
#     read-only flag — exempted.
#
# Known gaps (what this model does NOT close):
#   - Arbitrary Bash write-target resolution (`cp scratch src/x`,
#     `sed ... > src/x`, `tee src/x`) is not mechanically gated. Proving
#     where an arbitrary redirect or `cp` lands requires full shell-write-
#     target analysis; the file-write tools, git writes, and the closed
#     in-place-edit family below cover every mutation vector seen in the
#     transcript scan. The residual raw-Bash copy/redirect path is covered
#     by the reviewer-agent prose clause and the fail-closed principle that
#     reviewers work in /tmp.
#   - Combined short-option clusters (`sed -ni`, `perl -pi`) and GNU sed's
#     `--in-place` long form are not matched by the `-i`-prefix check below
#     — only literal `-i`/`-i<suffix>` tokens are. `black --check` /
#     `isort --check`/`--diff` / `ruff format --check` are denied
#     unconditionally rather than exempted, unlike terraform/rustfmt above
#     — a false deny on a genuinely read-only invocation of these three, not
#     a missed mutation.
#   - Does not resolve a fragment's effective cwd (unlike
#     require-worktree-for-git-writes.sh's -C/cd threading): a reviewer
#     never has a legitimate git-write or in-place-format target anywhere,
#     so cwd is irrelevant to the verdict here — this is the deliberate
#     lighter primitive per CLAUDE.md's default-suspect-over-powered-
#     primitives guidance.
#   - An agent reached only through an alias, wrapper script, or another
#     level of indirection this fragment-level scan cannot see is
#     undecidable, same class of gap as require-worktree-for-git-writes.sh.
#   - A git-write or in-place-edit reached through a QUOTED command name
#     (`bash -c "git checkout"`, `'sed' -i file`) is not caught: the word
#     scan sees the glued token (`"git`, `'sed`) and it matches neither the
#     bare name nor `*/name`. Same indirection class as the alias/wrapper gap
#     above. Deliberately accepted under the cooperative threat model — an
#     accidental reviewer mutation is a DIRECT command (which IS caught);
#     wrapping a command name in quotes to run it is not a cooperative-agent
#     behavior. Hardening the shared _lib_fragment_invokes_git for this would
#     also change require-worktree-for-git-writes.sh — out of scope here.
#   - macOS `/tmp` is a symlink to `/private/tmp`. If a future harness build
#     resolves `file_path` to its real path before invoking this hook, a
#     legitimate reviewer write under `/tmp/...` would miss the literal
#     `/tmp/*` exemption and be denied (fail-closed friction, not a safety
#     gap). Not observed on the current harness, which passes the path as
#     written.

set -uo pipefail

# emit_deny is defined before sourcing _lib.sh so it is available even if
# _lib.sh is absent. The guarded source below ensures a missing _lib.sh
# emits a deny rather than a silent allow or a bare exit 1.
emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by reviewer-tree-mutation hook: could not source _lib.sh — hook cannot evaluate reviewer discipline safely."
  exit 0
fi

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

# Local to this hook (not _lib.sh — these are in-place-edit-family word
# matchers, a different concern than _lib.sh's git-parsing helpers).

# Resolve a fragment's effective command word: skip leading env-var
# assignments (VAR=val) and a closed set of runner/wrapper words (plus each
# runner's connector sub-token), then return the first remaining word.
# `npx prettier`, `python -m black`, `xargs sed`, `sudo env X=1 isort` resolve
# to prettier/black/sed/isort; `grep black` / `echo isort` resolve to grep/echo.
#
# WHY command-word, not any-word (unlike the shared _lib_fragment_invokes_git
# used for git): the in-place-edit family names (black, isort, sed, ruff…) are
# common English/identifier words that legitimately appear as arguments in a
# reviewer's read-only commands (`grep black file`, `git log --grep isort`),
# so an any-word scan false-denies them. "git" is not a common argument word,
# so its any-word scan stays. Runner set is closed, same discipline as the
# _LIB_* enumerations — extended deliberately, not accreted.
_fragment_command_word() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local word cmd="" expect_after_runner=false
  for word in $fragment; do
    # Leading env-var assignment (VAR=val); precedes the command. A flag like
    # --write=false starts with '-' and does not match, so it is never eaten.
    if [[ "$word" =~ ^[A-Za-z_][A-Za-z0-9_]*= ]]; then
      continue
    fi
    if $expect_after_runner; then
      # A runner's own connector sub-token or flag (e.g. `poetry run`,
      # `python -m`, `npx --yes`) — skip it; the command is still ahead.
      case "$word" in
        run|exec|dlx|tool|-*) continue ;;
      esac
      expect_after_runner=false
    fi
    case "$word" in
      sudo|doas|env|command|time|nice|xargs|npx|pnpm|yarn|bunx|bun|pipx|uvx|uv|poetry|pipenv|rye|hatch|pdm|python|python2|python3|node|deno \
      |*/sudo|*/env|*/xargs|*/python|*/python2|*/python3|*/node|*/deno|*/uv|*/npx)
        expect_after_runner=true
        continue ;;
    esac
    cmd="$word"
    break
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  printf '%s' "$cmd"
}

# True iff the fragment's command word equals $2, or ends in "/$2" (an
# absolute/relative path invocation, e.g. /usr/bin/terraform).
_fragment_invokes_tool() {
  local fragment="$1" tool="$2"
  local cmd
  cmd=$(_fragment_command_word "$fragment")
  [[ -n "$cmd" && ( "$cmd" == "$tool" || "$cmd" == */"$tool" ) ]]
}

# True iff $2 appears in $1 as a standalone whitespace-delimited token
# (anchored to string edges or whitespace on both sides) — for exact-flag
# checks (--write, --fix, --check, fmt, format, check) where a real value
# never appends more non-space characters.
_fragment_has_token() {
  local fragment="$1" token="$2"
  [[ "$fragment" =~ (^|[[:space:]])${token}([[:space:]]|$) ]]
}

# True iff $1 contains a whitespace-delimited token STARTING with $2 — for
# flags whose value is attached with no separator (sed/perl's -i[SUFFIX]).
_fragment_has_token_prefix() {
  local fragment="$1" prefix="$2"
  [[ "$fragment" =~ (^|[[:space:]])${prefix} ]]
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
      agent-reviews/*|*/agent-reviews/*) exit 0 ;;
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
      if _fragment_invokes_tool "$fragment" terraform || _fragment_invokes_tool "$fragment" tofu; then
        if _fragment_has_token "$fragment" fmt \
          && ! _fragment_has_token "$fragment" -check \
          && ! _fragment_has_token "$fragment" -write=false; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'terraform/tofu fmt' rewrites files in place unless -check or -write=false is given. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      if _fragment_invokes_tool "$fragment" sed || _fragment_invokes_tool "$fragment" perl; then
        if _fragment_has_token_prefix "$fragment" -i; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'sed -i'/'perl -i' rewrites the file in place. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      if _fragment_invokes_tool "$fragment" gofmt && _fragment_has_token "$fragment" -w; then
        emit_deny "Blocked by reviewer-tree-mutation hook: 'gofmt -w' rewrites the source file in place. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      if _fragment_invokes_tool "$fragment" prettier && _fragment_has_token "$fragment" --write; then
        emit_deny "Blocked by reviewer-tree-mutation hook: 'prettier --write' rewrites the file in place. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      if _fragment_invokes_tool "$fragment" eslint && _fragment_has_token "$fragment" --fix; then
        emit_deny "Blocked by reviewer-tree-mutation hook: 'eslint --fix' rewrites the file in place. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      if _fragment_invokes_tool "$fragment" ruff; then
        if _fragment_has_token "$fragment" format; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'ruff format' rewrites files in place. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
        if _fragment_has_token "$fragment" check && _fragment_has_token "$fragment" --fix; then
          emit_deny "Blocked by reviewer-tree-mutation hook: 'ruff check --fix' rewrites files in place. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      if _fragment_invokes_tool "$fragment" black; then
        emit_deny "Blocked by reviewer-tree-mutation hook: 'black' rewrites files in place by default. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      if _fragment_invokes_tool "$fragment" isort; then
        emit_deny "Blocked by reviewer-tree-mutation hook: 'isort' rewrites files in place by default. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      if _fragment_invokes_tool "$fragment" rustfmt && ! _fragment_has_token "$fragment" --check; then
        emit_deny "Blocked by reviewer-tree-mutation hook: 'rustfmt' rewrites files in place unless --check is given. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi
    # <<< here-string, not < <(...) process substitution: _lib_split_fragments
    # emits no trailing newline for a single, unsplit fragment, and a
    # process-substitution `read` returns non-zero (loop body never runs) on
    # a final line with no newline delimiter. `$(...)` command substitution
    # strips any trailing newline and `<<<` re-adds exactly one, guaranteeing
    # the last fragment is newline-terminated. Mirrors deny-pii-in-commits.sh
    # and deny-private-project-refs.sh's identical `<<< "$(_lib_split_fragments ...)"` usage.
    done <<< "$(_lib_split_fragments "$COMMAND")"
    exit 0
    ;;
  *)
    exit 0
    ;;
esac
