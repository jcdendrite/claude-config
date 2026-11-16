#!/usr/bin/env bash
# hook-class: gate
# tier-threat-model: cooperative, irreversible
# Gate: review-only agents (the eight staff-*/ciso-reviewer personas, the
# non-specialist reviewers skill-fidelity-reviewer and
# comment-discipline-reviewer, plus the harness built-ins Explore/Plan — see
# _lib.sh's _LIB_REVIEW_ONLY_AGENTS) must never mutate the tree they are
# reviewing.
# Their Write tool's first sanctioned target is the caller's own <agent>-*.md findings file in an ignored agent-reviews directory.
# Known gaps below state that rule's reach by depth and by path form.
# Their Write tool's second sanctioned target is a new <agent>-*.md file the caller creates with the Write tool directly inside this session's /review-pr findings directory under /tmp.
# A Bash write is sanctioned only under /tmp.
# /dev/null is also exempt from the Bash write-target scan.
# Per the harness's own agent listing, which this repo cannot pin, `Plan` is the only roster member holding Bash.
# A consumer project's agent file with a roster persona's name overrides the shipped tools line and can give that persona Bash again.
# The gate is wired under both the Bash and Edit|Write|MultiEdit PreToolUse matchers in settings.json.
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
# work and returns immediately for every non-review-only caller (a main
# session started without a review-only `--agent`, code-writer,
# general-purpose, or any agent not in the closed set) — this keeps it off the
# latency budget for the overwhelmingly common call.
# A review-only agent's command of several fragments can exceed the
# claude-hook-review per-fire budget (<100ms), stated in
# plugins/claude-hook-review/skills/claude-hook-review/SKILL.md
# § "7. Performance budget".
# `.agent_type` names a subagent's type, or the `--agent` name of a main
# session started with one.
# It is a documented PreToolUse field (code.claude.com/docs/en/hooks) already
# consumed by nudge-error-mode-analysis.sh / nudge-handoff-near-context-cap.sh.
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
#   - ruff: `ruff format` denies on any invocation, like the pure-formatter
#     tier above.
#     Any invocation carrying --fix, --fix-only or --add-noqa denies, since
#     each writes to disk.
#     --add-noqa also denies in its `--add-noqa=REASON` form.
#     The flag alone decides, so bare `ruff --fix` denies with no `check` word.
#     `ruff check` alone stays allowed as read-only linting.
#
# worktree-ensure.sh is denied by command word.
# require-worktree-for-git-writes.sh's main-tree deny message advertises it.
# It fetches refs and creates a branch and a worktree.
# Its command text holds no git word for this scan to catch.
#
# Known gaps (what this model does NOT close):
#   - The Bash arm denies only the shapes this header names, so any other
#     write verb or form (`rm`, `ln`, the source side of `mv`, an inline
#     interpreter write such as `python3 -c`, a tool's own output flag, such
#     as `git diff --output=<path>`) is not inspected.
#     GH-1295 tracks this closed enumeration.
#     Only `Plan` reaches the Bash arm, with the qualifiers stated at the top of this header.
#   - Plain `ruff check` writes to disk when the target repo's ruff config
#     sets `fix = true`, or when the command passes `--config fix=true`.
#     This gap is accepted permanently.
#   - GH-751 is only partly closed: _fragment_raw_write_targets below
#     catches a `cp`/`mv`/`tee` write target only when it is the
#     fragment's sole or first command; a target behind a bare `&`
#     background operator (`cp /tmp/scratch.txt src/tracked_file.txt &
#     echo /tmp/x`) is not caught, because `_lib_split_fragments` does not
#     split a fragment on a bare `&`, so the word-walk still resolves `cp`
#     as the command word and reads a stale trailing word as the
#     destination.
#     A `>`/`>>` target behind a bare `&` is still caught, because the
#     target scan walks every word of the fragment (`echo hi & echo x >
#     src/y` denies).
#   - GH-811 tracks that `_lib_split_fragments` limitation; see
#     _fragment_raw_write_targets's own docstring below for its other
#     residual gaps (relative paths, symlinks, fd-numbered redirects,
#     `&>`, `cp -t DIR`, and `tee -`/`tee -- -file`).
#     Its symlinks entry is the /tmp link gap below.
#   - The same unsplit bare `&` can hide a command after it from the checks
#     that read a fragment's command word or first git subcommand:
#       - The git subcommand check reads the first git word only, so a git
#         write after the `&` passes when an earlier git word with an allowed
#         subcommand precedes it (`git log & git checkout -- x` reads as
#         `log`).
#       - A git write after a non-git prefix still denies (`sleep 1 & git
#         checkout -- x`), because the first git word is then the write.
#       - The command-word arms (`sed -i`, `perl -i`, the formatters, `ruff`,
#         `eslint`, `terraform fmt`, `worktree-ensure.sh`) read the
#         fragment's resolved command word only, so a command after the `&`
#         passes whatever precedes it (`echo hi & sed -i s/a/b/ x`).
#   - A shell reserved word, `{`, `!`, a case-arm label (`x)`), `((` or
#     `coproc` at the start of a fragment is not split from the command after
#     it, so the fragment's command word is that word (`do sed -i s/a/b/ x`).
#       - The evaders verified are `do`, `then`, `else`, `if`, `elif`, `while`,
#         `until`, `!`, `{`, a case arm, `coproc` and `((`.
#       - `time sed -i s/a/b/ x` still denies, because `time` is a runner word.
#       - Only the first command after the keyword on the same line evades.
#       - `do echo $f; sed -i s/a/b/ x` denies, as does a body command on its
#         own line.
#       - Every command-word arm misses it: `sed -i`, `perl -i`, the
#         formatters, `ruff format`/`--fix`, `eslint --fix`, `terraform`/`tofu
#         fmt`, `worktree-ensure.sh`, and the `cp`/`mv`/`tee` destinations.
#       - A `>`/`>>` redirect in the same body is still caught.
#       - The git scan reads any word of the fragment, so a git write there
#         still denies.
#   - The `tee` destination walk matches the lowercase word only, while the
#     command-word gate is case-insensitive. `TEE src/x` and `Tee src/x`
#     allow, while `CP scratch src/x` and `MV scratch src/x` deny.
#   - These write shapes are not inspected on any fragment, and the hook
#     allows each:
#       - A redirect glued to the word before it (`echo x>src/y`), because
#         only a word that starts with `>` reads as a redirect.
#       - A `>|` clobber redirect (`echo x >| src/y`), because
#         _lib_split_fragments splits at the `|`.
#       - A redirect target built by command substitution, `$(...)` or
#         backticks (`echo x > $(printf src/y)`), because _lib_split_fragments
#         splits at both spellings.
#   - A symlink or hard link under /tmp launders the Bash arm's /tmp exemption.
#     GH-1295 tracks it. The facts of this one gap:
#       - The Bash arm matches the literal `/tmp/*` text without resolving links.
#       - A write through such a link changes the linked file, which can be any
#         file the user can write, not only a tracked one.
#       - Resolving the path before the match would false-deny every legitimate
#         macOS /tmp write, because /tmp is a symlink to /private/tmp there.
#       - A cooperative agent can create such a link and then write through it without noticing, so this gap is not waived on cooperative grounds.
#       - Tests in test_deny_reviewer_tree_mutation.py pin this gap's current
#         allow verdicts for `Plan`.
#   - The Write arm accepts the caller's own `<agent>-*.md` file in an ignored directory named agent-reviews at any depth.
#     The unanchored ignore pattern findings-path-suffix.sh writes ignores every directory of that name in the repository, so the reach is any such directory.
#     That reach includes an agent-reviews directory anywhere under .claude/, for example under .claude/commands/, .claude/rules/, .claude/agents/ or .claude/plans/.
#     Claude Code may load a file written there.
#     The any-depth reach is accepted as debt.
#     The reach also includes `<repo>/.git/refs/heads/agent-reviews/<agent>-*.md`, because `git check-ignore -q` exits 0 for it.
#   - A Write path starting with `~` or `:/` passes the case globs and the name predicate unchanged.
#     `git check-ignore -q` exits 0 for `~/agent-reviews/<agent>-x.md` and `:/agent-reviews/<agent>-x.md`, so the ignore check does not deny them.
#     git check-ignore judges the literal string relative to the cwd, where git expands no `~` and reads a leading `:/` as pathspec magic.
#     The ignore check therefore does not describe the file written.
#     If the Write tool expands `~`, the file would land outside the repo.
#     Whether the Write tool expands `~`, and whether the hook payload carries the raw string, is unverified.
#   - Combined short-option clusters (`sed -ni`, `perl -pi`) and GNU sed's
#     `--in-place` long form are not matched by the `-i`-prefix check below
#     — only literal `-i`/`-i<suffix>` tokens are, a missed mutation for the
#     dual-use text tools. Separately, every pure formatter's read-only check
#     mode (`rustfmt --check`, `prettier --check`, `terraform fmt -check`,
#     `black --check`, `ruff format --check`) is denied by the unconditional
#     rule above — a false deny on a genuinely read-only invocation, not a
#     missed mutation (see Grounding).
#   - These shapes deny as raw writes, an accepted false deny:
#       - A `>` that starts a word inside quoted text or a heredoc body
#         (`git log --grep 'a > b'`).
#         docs/design-decisions/command-text-gates-accept-quoted-text-over-deny.md
#         records why.
#       - A redirect to `/dev/stderr` or `/dev/stdout`, because the raw-write
#         check exempts only `/dev/null` and `/tmp/*` targets.
#       - `>&2`, `>& /dev/null` and `>& /tmp/x`, because the check reads the
#         text after `>` as the target (`&`, `&2`), which no exemption matches.
#   - Does not resolve a fragment's effective cwd (unlike
#     require-worktree-for-git-writes.sh's -C/cd threading): a reviewer
#     never has a legitimate git-write or in-place-format target anywhere,
#     so cwd is irrelevant to the verdict here — this is the deliberate
#     lighter primitive per CLAUDE.md's default-suspect-over-powered-
#     primitives guidance.
#   - An agent reached only through an alias, wrapper script, or another
#     level of indirection this fragment-level scan cannot see is
#     undecidable, same class of gap as require-worktree-for-git-writes.sh.
#     For worktree-ensure.sh the residual is every command whose command
#     word, after _lib.sh's command-word resolution, is not the script.
#     That includes wrappers outside the runner skip list (`bash`, `sh`,
#     `source`, `timeout N`, `exec`, `nohup`, `setsid`), syntax the fragment
#     splitter does not unwrap (the reserved-word and bare-`&` entries above),
#     and glob- or variable-built command words.
#     Each is undecidable here, the same as the `bash -c` gap below.
#     The class is accepted.
#   - The `venv-run.sh` wrapper word is stripped only from fragments whose
#     command word is the wrapper, a guard applied at the call site.
#     Under that guard `scripts/venv-run.sh ruff format` resolves to `ruff`,
#     while `cp /tmp/x scripts/venv-run.sh` keeps its write target because
#     its command word is `cp`, not the wrapper.
#     The strip is local to this gate and is not part of _lib.sh's runner
#     words, which also feed gates that skip a check on a match.
#     `bash scripts/venv-run.sh ruff format` is not unwrapped and stays in the
#     accepted indirection class above.
#   - The strip removes the first word whose basename is `venv-run.sh`, not
#     the command word the guard classified, so
#     `FOO=a/venv-run.sh scripts/venv-run.sh ruff format x.py` passes.
#     The cooperative tier accepts this.
#   - A redirect-led command word (`2>w ruff format`, `<w ruff format`) is
#     allowed, with or without the wrapper, because the command-word resolver
#     skips no redirect word. The unwrapped `2>/dev/null ruff format` is
#     allowed the same way.
#   - A git-write or in-place-edit reached through a command name quoted
#     directly in the Bash arm's own command text (`'sed' -i file`) IS
#     caught: the Bash arm strips quote characters from $COMMAND before
#     splitting into fragments, so the word scan sees the bare token. A
#     command name reached only through a nested shell boundary this scan
#     never executes (`bash -c "sed -i s/a/b/ x"`) is still undecidable — same
#     indirection class as the alias/wrapper gap above.
#   - A gated word, write flag, or write target split across a
#     backslash-newline continuation is caught, because the Bash arm also
#     scans a copy of the stripped text with every backslash-newline pair
#     deleted.
#   - The Write arm's agent-reviews findings-file exemption is not a pure string match:
#     it checks that the file name starts with the caller's own agent type,
#     that its parent directory is agent-reviews itself (so a file in any
#     subdirectory of agent-reviews is denied), and, through
#     `_lib_path_ignore_status`, that the path is actually ignored.
#     Any failure of the ignore check — git absent, the resolved cwd not a
#     repo, the timeout firing — denies rather than allows (fail-closed
#     friction on an unusual environment, not a missed mutation).
#     An exported GIT_LITERAL_PATHSPECS, GIT_GLOB_PATHSPECS, GIT_NOGLOB_PATHSPECS
#     or GIT_ICASE_PATHSPECS in a reviewer session makes the ignore check exit 128.
#     So every findings write denies and the reviewer returns inline.
#     A symlinked agent-reviews directory also makes the ignore check exit 128, so the write denies.
#   - The /review-pr findings-directory exemption is create-only and Write-only.
#     It accepts a path only when all of these hold:
#       - The tool is Write.
#       - The path names nothing yet (not an existing file, symlink or dangling symlink).
#       - The parent is exactly the directory _lib_review_pr_reviewer_findings_dir names for the payload's session_id.
#       - The name starts with the caller's agent type.
#       - The name ends in .md.
#       - That directory exists.
#       - That directory is not a symlink.
#     Edit and MultiEdit into that directory deny.
#     The create-only check is what closes the symlink and hard-link gap for the file itself, apart from a race between the check and the write.
#     The directory's owner and mode are not checked.
#     The exemption assumes a subagent's PreToolUse payload carries the dispatching session's session_id.
#     That assumption is unverified until a live probe; if the payload carried another id, every such write would deny.
#   - macOS `/tmp` is a symlink to `/private/tmp`.
#     If a harness build resolves `file_path` to its real path before invoking this hook, a findings write under `/tmp/...` would miss the literal `/tmp/*` arm and deny.
#     That is fail-closed friction, not a safety gap. [unverified] The current harness passes the path as written.
#   - The Bash arm's COMMAND_UNQUOTED sed/tr strip failure fails closed: its
#     exit status is checked and denies with an explicit message rather than
#     falling through to this hook's normal "no gated fragment matched"
#     allow path.
#   - Waived under the cooperative tier:
#       - A Write, Edit or MultiEdit payload with no file_path is allowed.
#       - The Bash arm's denial text names the Write tool, which Plan may lack (GH-1295).
#       - A flag-only git form such as `git --version` is denied, because its subcommand is empty and the denial text reads `'git '` (GH-1295).
#       - The Write arm's agent-reviews branch judges the path string and the ignore check, never the inode.
#         A symlinked own-name leaf or a hard-linked own-name leaf is not inspected by this hook.
#         Any agent that holds Bash, `Plan` included, can plant such a link.
#         Whether the Write tool refuses a symlink leaf or a hard-linked leaf it did not read is unverified here.
#       - An allowed git subcommand can run a command named by one of its own flags, or by a `-c <key>=<value>` config setting whose value git runs as a command.
#         Examples: `git grep -O<cmd>`, `git ls-remote --upload-pack=<cmd>`, `git -c core.fsmonitor=<cmd> status`.
#         The hook allows each example, because none carries a redirect word and each subcommand is in the allowed git set.
#         Whether git actually runs the command is the unconfirmed part, because no run has tried it.

set -uo pipefail

DENY_GATE_LABEL="reviewer-tree-mutation"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh. For the recovery, see docs/hooks.md § \"Gate deadlock recovery\" in the claude-config repository; the user, not the agent, runs ./install.sh from the claude-config checkout in a separate terminal only if that runbook says so."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON. Refusing to evaluate reviewer discipline under malformed input."

# .agent_type is the trust-boundary field the entire gate decision hinges on.
# _lib_parse_tool_input_or_deny already reads it fail-closed (denying on a
# jq failure before returning), so AGENT_TYPE is never empty here as a
# silent read failure — only as a genuinely absent field.

# Fast common-path exit, BEFORE any tool-specific work: a main session started
# without a review-only `--agent`, code-writer, general-purpose, and every
# agent outside the closed review-only set pass through unconditionally
# regardless of tool or command.
_lib_is_review_only_agent "$AGENT_TYPE" || exit 0

# Every double-quoted denial string in this hook must not hold a backtick: inside a double-quoted string it runs a command substitution.
DENIAL_DISCIPLINE="Reviewers are read-only on the tree under review. Treat a hook denial as final. Use Read, Grep, or Glob for a read the hook misjudges. Do not retry any other denied action through a script, another command form, or another tool."
# The Bash arm's denial text. `Plan` is the only roster member that reaches it, with the qualifiers stated at the top of this header.
SANCTIONED_ALTERNATIVE="$DENIAL_DISCIPLINE Confirm a claim by reading and tracing the code before running anything. Scratch work belongs only in a fresh directory you created under /tmp, holding only files you create there. Spell a /tmp path out literally, because this hook matches write targets as written. Never overwrite or replace an existing path, even one you created; write a new file under a new name instead. A write through a symlink or hard link changes the linked file, wherever it lives, so a /tmp path can still change a file outside /tmp. The only sanctioned in-tree write is the findings file (agent-reviews/<agent>-<epoch>-<slug>.md, via the Write tool)."

# The Write arm's denial text: a reviewer has no scratch directory, so it names only the findings file.
WRITE_ARM_ALTERNATIVE="$DENIAL_DISCIPLINE Your only write is the findings_path your dispatch handed you, via the Write tool. If that write is denied, return your findings inline and write nothing else."

# Local to this hook, not _lib.sh: this prefix matcher is the only
# in-place-edit-family word matcher without a second caller elsewhere.

# True iff $1 contains a whitespace-delimited token STARTING with $2 — for
# flags whose value is attached with no separator (sed/perl's -i[SUFFIX],
# ruff's --add-noqa=REASON).
_fragment_has_token_prefix() {
  local fragment="$1" prefix="$2"
  [[ "$fragment" =~ (^|[[:space:]])${prefix} ]]
}

# Removes the first word whose basename is exactly `venv-run.sh` (bare or
# path-prefixed) from the fragment held in the variable named by $1, so the
# tool it launches becomes the command word. Leaves the variable untouched
# when no word matches. The match is case-insensitive, like
# _lib_fragment_invokes_tool.
#
# Caller contract: quote-blind, same as every other word-walk in this file.
# The function strips the first wrapper-named word wherever it sits, so the
# caller must guarantee that the fragment's command word is the wrapper and
# not a redirect such as `2>x/venv-run.sh`.
_strip_venv_run_wrapper_word() {
  local target_name="$1"
  local text="${!target_name}"
  local saved_opts=$-
  set -f
  shopt -s nocasematch
  local word rebuilt="" stripped=false
  for word in $text; do
    if ! $stripped && [[ "$word" == venv-run.sh || "$word" == */venv-run.sh ]]; then
      stripped=true
      continue
    fi
    rebuilt+=" $word"
  done
  rebuilt="${rebuilt# }"
  shopt -u nocasematch
  if [[ "$saved_opts" != *f* ]]; then set +f; fi
  if $stripped; then printf -v "$target_name" '%s' "$rebuilt"; fi
}

# True iff the fragment's command word is the venv-run.sh wrapper and not a
# redirect operator word (`>x`, `2>x`, `<x`) whose destination merely ends in
# `/venv-run.sh`.
_fragment_launches_venv_run_wrapper() {
  local fragment="$1"
  _lib_fragment_invokes_tool "$fragment" venv-run.sh || return 1
  local command_word
  command_word=$(_lib_fragment_command_word "$fragment")
  # In a variable because an unquoted `<` or `>` inside `[[ =~ ]]` is a bash syntax error.
  local redirect_word_pattern='^[0-9]*[<>]'
  [[ ! "$command_word" =~ $redirect_word_pattern ]]
}

# Prints, one per line, every destination a `cp`/`mv`/`tee` invocation or a
# bare `>`/`>>` shell redirect in $1 writes to.
# Catches a cp/mv/tee target only when that command is the fragment's sole
# or first command — a target hidden behind a bare `&` background operator
# in the same fragment is invisible (GH-811 tracks the underlying
# _lib_split_fragments limitation this depends on; see the header's "Known
# gaps" section).
# Catches a `>`/`>>` target anywhere in the fragment, a bare `&` included.
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
  local word_count="${#words[@]}"
  local i=0 next
  while [ "$i" -lt "$word_count" ]; do
    word="${words[$i]}"
    case "$word" in
      '>'|'>>')
        next=$((i + 1))
        [ "$next" -lt "$word_count" ] && printf '%s\n' "${words[$next]}"
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
  if [ "$word_count" -gt 0 ] && (_lib_fragment_invokes_tool "$fragment" cp || _lib_fragment_invokes_tool "$fragment" mv); then
    printf '%s\n' "${words[$((word_count - 1))]}"
  fi
  if [ "$word_count" -gt 0 ] && _lib_fragment_invokes_tool "$fragment" tee; then
    local seen_tee=false
    i=0
    while [ "$i" -lt "$word_count" ]; do
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

# Only the Write tool creates a file.
# Local to this hook, not _lib.sh: no other hook needs it.
_may_create_own_review_pr_findings_file() {
  local tool_name="$1" file_path="$2" session_id="$3" agent_type="$4" findings_dir
  [[ "$tool_name" == Write ]] || return 1
  findings_dir=$(_lib_review_pr_reviewer_findings_dir "$session_id") || return 1
  [[ "${file_path%/*}" == "$findings_dir" ]] || return 1
  [[ "${file_path##*/}" == "$agent_type"-*.md ]] || return 1
  [[ ! -e "$file_path" && ! -L "$file_path" ]] || return 1
  [[ -d "$findings_dir" && ! -L "$findings_dir" ]]
}

case "$TOOL_NAME" in
  Write|Edit|MultiEdit)
    # _lib_parse_tool_input_or_deny already reads .tool_input.file_path
    # fail-closed, so FILE_PATH is never empty here as a silent read
    # failure — only as a genuinely absent field.
    [ -z "$FILE_PATH" ] && exit 0
    case "$FILE_PATH" in
      # Traversal guard FIRST, mirroring require-worktree-for-file-writes.sh:
      # a case glob matches the literal string and does not resolve `..`, so
      # `agent-reviews/../src/x` would satisfy the `agent-reviews/*` prefix
      # below while actually resolving to a tracked repo file. Reject any path
      # with a `..` segment (leading `../`, embedded `/../`, or trailing `/..`)
      # before the exemption — a traversal path falls through to the deny.
      # `..` as a bare segment only; a filename like `a..b` is fine.
      ../*|*/../*|*/..) ;;
      # Matched as a /-delimited path component (leading "agent-reviews/"
      # or a nested "*/agent-reviews/*"), not a substring — a file literally
      # named agent-reviews-notes.md must not be exempted.
      agent-reviews/*|*/agent-reviews/*)
        # Every non-matching arm is a bare `;;`, so control falls to the final deny below.
        case "${FILE_PATH%/*}" in
          agent-reviews|*/agent-reviews)
            case "${FILE_PATH##*/}" in
              "$AGENT_TYPE"-*.md)
                # _lib_parse_tool_input_or_deny already reads .cwd fail-closed,
                # so CWD is never empty here as a silent read failure — the
                # zero-length check below is for a genuinely absent field.
                # An empty CWD denies because, on bash 5.2, `cd ''` succeeds in
                # place, so the ignore check would silently run in whatever
                # directory the hook process happens to be in. Deliberately
                # stricter than require-worktree-for-git-writes.sh's
                # `[ -z "$CWD" ] && CWD="$PWD"` fallback: that hook can default
                # an unresolvable cwd to allow, while this hook's invariant is
                # "the ignore state is confirmed", which an unresolvable cwd
                # leaves unconfirmed.
                if [ -z "$CWD" ]; then
                  emit_deny "the tool payload carried no .cwd — refusing to check whether agent-reviews/ is ignored without knowing which repo to check."
                  exit 0
                fi
                # A findings file is only safe when agent-reviews/ is actually
                # ignored in the target repo; _lib_path_ignore_status holds the
                # check's status contract.
                _lib_path_ignore_status "$CWD" "$FILE_PATH"
                IGNORE_CHECK_STATUS=$?
                case "$IGNORE_CHECK_STATUS" in
                  0) exit 0 ;;
                  1)
                    emit_deny "'agent-reviews/' is not actually ignored in this repo — findings_path dispatch is unsafe here. Fall back to inline output; do not create or modify ignore rules yourself — that is the dispatching skill's job, not the reviewer's."
                    exit 0
                    ;;
                  3)
                    emit_deny "could not confirm 'agent-reviews/' is ignored — the payload's .cwd ('$CWD') does not resolve to a directory this process can enter."
                    exit 0
                    ;;
                  *)
                    emit_deny "could not confirm 'agent-reviews/' is ignored (exit $IGNORE_CHECK_STATUS — not a git repo, or the check failed) — refusing to allow the write under an unconfirmed invariant."
                    exit 0
                    ;;
                esac
                ;;
            esac
            ;;
        esac
        ;;
      # The one sanctioned /tmp target: a new file of the caller's own in this
      # session's /review-pr findings directory. Any other /tmp path, and any
      # failed check, falls to the final deny.
      /tmp/*)
        if _may_create_own_review_pr_findings_file "$TOOL_NAME" "$FILE_PATH" "$SESSION_ID" "$AGENT_TYPE"; then
          exit 0
        fi
        ;;
    esac
    emit_deny "$TOOL_NAME targets '$FILE_PATH', which is not your own findings file. $WRITE_ARM_ALTERNATIVE"
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
    # git symbolic-ref HEAD <ref>) are NOT read-only here. Subtract them.
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
      emit_deny "could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned command for a review-only agent."
      exit 0
    fi

    # The shell deletes a backslash-newline pair, so the joined copy is what
    # runs, and a write flag continued onto the next line belongs to its tool.
    # The unjoined copy stays because the quote strip turns an escaped `\\` at
    # a line end into a lone `\`, which joining would wrongly fuse with the
    # next line.
    # The fragment loop only denies, so scanning both copies can only add
    # denials.
    BACKSLASH_NEWLINE=$'\\\n'
    COMMAND_UNQUOTED_JOINED=${COMMAND_UNQUOTED//"$BACKSLASH_NEWLINE"/}
    FRAGMENT_SCAN_UNQUOTED="$COMMAND_UNQUOTED"
    if [ "$COMMAND_UNQUOTED_JOINED" != "$COMMAND_UNQUOTED" ]; then
      FRAGMENT_SCAN_UNQUOTED="$COMMAND_UNQUOTED"$'\n'"$COMMAND_UNQUOTED_JOINED"
    fi

    FRAGMENTS=$(_lib_split_fragments "$FRAGMENT_SCAN_UNQUOTED")
    FRAGMENTS_SPLIT_EXIT=$?
    if [ "$FRAGMENTS_SPLIT_EXIT" -ne 0 ]; then
      emit_deny "could not split the command into fragments (exit ${FRAGMENTS_SPLIT_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned command for a review-only agent."
      exit 0
    fi
    while IFS= read -r fragment; do
      if _fragment_launches_venv_run_wrapper "$fragment"; then
        _strip_venv_run_wrapper_word fragment
      fi
      [ -z "$fragment" ] && continue

      if _lib_fragment_invokes_git "$fragment"; then
        subcmd=$(_lib_extract_git_subcmd "$fragment")
        # Unlike require-worktree-for-git-writes.sh, cwd is irrelevant here:
        # a reviewer never has a legitimate git-write target anywhere, so
        # any non-read-only subcommand denies regardless of where it runs.
        if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
          emit_deny "'git $subcmd' is not a read-only git subcommand for a review-only agent, and review-only agents never write git state anywhere (worktree or main tree). $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
        # An allowed git fragment falls through to the in-place-edit and
        # raw-write checks below.
      fi

      # Closed in-place-edit family. Each tool word is matched
      # case-insensitively against the fragment's resolved command word.
      # The match is the exact word, or a word ending in "/<tool>"
      # (absolute/relative path invocation).
      # The git word above is the exception: it uses an any-word scan.
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
          emit_deny "'$formatter' reformats files and a review-only agent never reformats the tree under review. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      done

      # terraform / tofu are multi-command tools; only the fmt subcommand
      # writes. Gate on the fmt subcommand so read-only subcommands (validate,
      # plan) stay available, but deny fmt regardless of -check / -write=false
      # — same over-deny-the-check-mode stance as the pure formatters above.
      if _lib_fragment_invokes_tool "$fragment" terraform || _lib_fragment_invokes_tool "$fragment" tofu; then
        if _lib_fragment_has_token "$fragment" fmt; then
          emit_deny "'terraform/tofu fmt' reformats files and a review-only agent never reformats the tree under review. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      # The command word is matched, so reading the script (`cat`, `grep`,
      # `bash -n`) stays allowed.
      if _lib_fragment_invokes_tool "$fragment" worktree-ensure.sh; then
        emit_deny "The worktree gate's pointer to worktree-ensure.sh does not apply to review-only agents: it fetches refs and creates a branch and a worktree, and a review-only agent never writes git state. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      # Linters and text tools KEEP write-flag gating — unlike the pure
      # formatters above, their bare / read-only forms (`ruff check`, `eslint`
      # without --fix, `sed`/`perl` without -i) are ordinary review actions a
      # reviewer legitimately runs, so only the mutating flag/subcommand denies.
      if _lib_fragment_invokes_tool "$fragment" ruff; then
        # Deny `ruff format` and any invocation carrying a write flag.
        # --fix and --fix-only rewrite files, and --add-noqa inserts `# noqa`
        # comments.
        # --fix and --fix-only match as exact tokens, so `--fixable` stays
        # allowed.
        # --add-noqa matches as a token prefix, so `--add-noqa=REASON` denies
        # too.
        # No `check` token is required, so bare `ruff --fix` denies.
        if _lib_fragment_has_token "$fragment" format \
          || _lib_fragment_has_token "$fragment" --fix \
          || _lib_fragment_has_token "$fragment" --fix-only \
          || _fragment_has_token_prefix "$fragment" --add-noqa; then
          emit_deny "'ruff format' / 'ruff --fix' / 'ruff --add-noqa' rewrites files in place. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      if _lib_fragment_invokes_tool "$fragment" eslint && _lib_fragment_has_token "$fragment" --fix; then
        emit_deny "'eslint --fix' rewrites the file in place. $SANCTIONED_ALTERNATIVE"
        exit 0
      fi

      if _lib_fragment_invokes_tool "$fragment" sed || _lib_fragment_invokes_tool "$fragment" perl; then
        if _fragment_has_token_prefix "$fragment" -i; then
          emit_deny "'sed -i'/'perl -i' rewrites the file in place. $SANCTIONED_ALTERNATIVE"
          exit 0
        fi
      fi

      # GH-751: raw-write-target check against the Bash arm's /tmp scratch allowance.
      #   - Base rule: a cp/mv/tee destination or a `>`/`>>` shell redirect
      #     that does not literally start with /tmp/ writes outside the one
      #     sanctioned reviewer scratch location, and denies.
      #   - Does not exempt agent-reviews/*: the ignore-state confirmation
      #     that exemption depends on (_lib_path_ignore_status) has no
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
            emit_deny "'$fragment' writes to '$raw_target', a path-traversal write target outside /tmp. $SANCTIONED_ALTERNATIVE"
            exit 0
            ;;
          /tmp/*) continue ;;
        esac
        emit_deny "'$fragment' writes to '$raw_target', which is outside /tmp. $SANCTIONED_ALTERNATIVE"
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

# shellcheck disable=SC2317 # Every arm above exits; this `exit 0` backstops a later arm that does not.
exit 0
