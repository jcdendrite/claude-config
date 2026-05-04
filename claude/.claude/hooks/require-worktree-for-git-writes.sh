#!/bin/bash
# Gate: require git write operations to happen inside a linked worktree,
# not the main working tree. Opt-in per-repo via a committed
# .claude/worktree-required sentinel file at the repo root.
#
# Motivation: concurrent Claude Code sessions on the same working tree can
# race — e.g. one session's `git reset --hard` silently wipes another's
# uncommitted edits. Working in linked worktrees (`git worktree add`)
# isolates each session's state.
#
# Allow list: ~26 known read-only git subcommands, plus `worktree` (so the
# bootstrap `git worktree add` isn't denied on the main tree). Anything
# else is denied when run from the main working tree of an opted-in repo.
# Allowed unconditionally inside a linked worktree.

# Defensive: prevent GIT_DIR / GIT_WORK_TREE env overrides from making the
# main tree impersonate a linked worktree via rev-parse output.
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE

INPUT=$(cat)
COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty' 2>/dev/null)
JQ_EXIT=$?
CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' \
    "$reason_json"
}

# Returns success when the command chains `cd ... <op> ... git ...`. The
# hook reads cwd from Claude Code's session-persisted bash state (set by
# prior Bash calls), not from the inline cd in the current command, because
# the hook fires before the subshell runs. When this shape is detected the
# effective cwd at the time git runs cannot be determined from the tool-input
# JSON alone. Pattern requires `cd` as a word, then a chain operator, then
# `git` somewhere later — avoids false positives on paths containing `cd`.
command_chains_cd_then_git() {
  printf '%s' "$1" | grep -qE '(^|[[:space:]])cd[[:space:]].*(&&|\|\||;).*git'
}

# When the command chains `cd ... <op> ... git ...`, the agent likely
# expected the inline `cd` to land inside a worktree. Returns a
# self-correction note for the agent when this pattern is detected;
# empty otherwise.
cwd_anchor_note_if_chained() {
  if command_chains_cd_then_git "$1"; then
    printf '%s' " If you just chained 'cd /path/to/worktree && git ...' and expected the inline cd to land you in the worktree: this hook reads cwd from Claude Code's session-persisted bash state (set by prior Bash calls), not from your inline cd, because the hook fires before the subshell runs. Anchor cwd by running 'cd /path/to/worktree' as its own Bash call first, then retry the git op in a follow-up call."
  fi
}

# When the command uses `git -C <path> <write-op>` from the main tree,
# the agent likely expected the -C path to be treated as the working
# tree. The hook checks the session-persisted CWD (from the tool input
# JSON) — not the -C path — so the op is blocked even when -C points at
# a linked worktree. Returns a self-correction note for the agent when
# this pattern is detected; empty otherwise.
#
# Detection is scoped to `-C` in the GLOBAL flag position only —
# subcommand-level uses (`git commit -C HEAD`, `git diff -C`, etc.)
# do not trigger the note, since the hint would not apply there.
# Mirrors the flag-skip table in extract_git_subcmd so both parsers
# stay consistent. Globbing is disabled around the loop so that an
# input like "git * -C foo" can't glob against cwd contents.
git_C_note_if_present() {
  local command="$1"
  fragment_invokes_git "$command" || return
  local after_git="${command#*git}"
  local saved_opts=$-
  set -f
  local skip_next=false found=false
  for word in $after_git; do
    if $skip_next; then
      skip_next=false
      continue
    fi
    case "$word" in
      -C)
        found=true
        break ;;
      -c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env)
        skip_next=true ;;
      -*)
        ;;
      *)
        break ;;  # subcommand reached; any -C past here is subcommand-scoped
    esac
  done
  # Note: detects -C in the first git invocation only. A command like
  # `git status && git -C ...` won't trigger — the loop stops at the
  # first git's subcommand and never reaches the second git's flags.
  if [[ "$saved_opts" != *f* ]]; then
    set +f
  fi
  if $found; then
    printf '%s' " If you used 'git -C <path>' expecting the -C path to be treated as the working tree: this hook reads cwd from Claude Code's session-persisted bash state (set by prior Bash calls), not from the -C path, because -C only retargets git's own working directory and doesn't change the session cwd. Anchor cwd by running 'cd /path/to/worktree' as its own Bash call first, then retry the git op in a follow-up call."
  fi
}

# Fail-closed on malformed input: if jq couldn't parse the stdin JSON, we
# can't tell what Claude is about to run, so deny rather than silently allow.
if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "Blocked by worktree-enforcement hook: could not parse tool-input JSON. Refusing to evaluate git discipline under malformed input."
  exit 0
fi

# Fast-path: commands that don't mention `git` as a word are not our
# concern. A plain `*git*` substring check false-positives on `.github`,
# `.gitignore`, `github.com`, `longitude`, and similar, blocking harmless
# reads like `ls .github/workflows/`. Require a non-alnum boundary (or
# string edge) on both sides so `git` fires only as a command word.
if ! [[ "$COMMAND" =~ (^|[^[:alnum:]])git([^[:alnum:]]|$) ]]; then
  exit 0
fi

# Find the repo. Outside a git repo, nothing to enforce.
REPO_ROOT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# Per-repo opt-in: only enforce if the repo has committed the sentinel.
if [ ! -f "$REPO_ROOT/.claude/worktree-required" ]; then
  exit 0
fi

# A chained `cd ... && git ...` makes the effective cwd at the time git
# runs unknowable from the tool-input JSON alone — the hook reads the
# session-persisted cwd from prior Bash calls, not the cwd the inline cd
# would produce. Deny regardless of the persisted cwd so that the hook's
# decision is never based on stale state. The agent fix is to anchor cwd
# with a standalone Bash call before the git op.
if command_chains_cd_then_git "$COMMAND"; then
  emit_deny "Blocked by worktree-enforcement hook: the command chains 'cd ... && git ...' and this hook cannot determine the effective cwd at the time git runs — it reads the session-persisted cwd (from prior Bash calls), not the cwd produced by the inline cd, because the hook fires before the subshell runs. This repo has opted into worktree discipline (.claude/worktree-required is committed). Anchor cwd by running 'cd /path/to/worktree' as its own Bash call first, then retry the git op in a follow-up call."
  exit 0
fi

# "Am I in a linked worktree?" check. For the main working tree,
# --git-dir and --git-common-dir return the same absolute path. For a
# linked worktree, --git-dir points at <common>/worktrees/<name> while
# --git-common-dir still points at <common>. Comparing the two is robust
# against path-substring false positives (e.g. a repo literally at
# ~/code/worktrees/myrepo) and env-var spoofing.
GIT_DIR_ABS=$(cd "$CWD" 2>/dev/null && git rev-parse --absolute-git-dir 2>/dev/null)
GIT_COMMON_DIR=$(cd "$CWD" 2>/dev/null && git rev-parse --path-format=absolute --git-common-dir 2>/dev/null)
if [ -n "$GIT_DIR_ABS" ] && [ -n "$GIT_COMMON_DIR" ] && [ "$GIT_DIR_ABS" != "$GIT_COMMON_DIR" ]; then
  exit 0
fi

# From here on we are in the MAIN working tree of an opted-in repo.
# Only read-only git subcommands are allowed; everything else is denied.

readonly ALLOWED_SUBCMDS=(
  blame
  branch          # "git branch" lists; creating/deleting takes flags
  cat-file
  check-attr      # read-only attribute lookup
  check-ignore    # read-only gitignore query
  check-mailmap   # read-only mailmap lookup
  check-ref-format # read-only ref name validation
  count-objects
  describe
  diff
  fetch           # updates remote-tracking refs only, not working tree
  for-each-ref
  fsck
  help
  log
  ls-files
  ls-remote
  ls-tree
  name-rev
  reflog
  remote
  rev-list
  rev-parse
  shortlog
  show
  status
  tag             # "git tag" lists; creating takes flags — acceptable risk
  var             # read-only git variable lookup
  verify-commit
  verify-tag
  version
  worktree        # bootstrap for this whole mechanism — don't block it
)
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

# Decide whether a fragment actually invokes `git`, not just mentions it
# as a substring of a path or URL. Scans whitespace-separated words and
# returns success iff any word equals `git` or ends in `/git` (absolute
# path form, e.g. `/usr/bin/git`). Env-var prefixes (`FOO=1 git ...`),
# `env`/`sudo` prefixes, and `git` as the nth word are all handled by
# walking every word rather than just the first.
#
# Rejects: `ls .github/workflows/`, `cat .gitignore`, `grep github.com`,
# `./git-foo` (not `git` and not `*/git`). Accepts: `git log`, `sudo git
# commit`, `FOO=1 git push`, `/usr/bin/git status`.
fragment_invokes_git() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  local found=false word
  for word in $fragment; do
    if [[ "$word" == "git" || "$word" == */git ]]; then
      found=true
      break
    fi
  done
  if [[ "$saved_opts" != *f* ]]; then
    set +f
  fi
  $found
}

# Extract the git subcommand from a fragment like "git -C path commit -m foo".
# Strips global flags that consume the next word, skips other flags, returns
# the first bare word — the subcommand. Empty output means we couldn't find
# one, which is a parse failure and triggers fail-closed deny.
#
# Globbing is explicitly disabled for the loop so that an input like
# "git * log" can't glob against cwd contents to hide the real subcommand.
extract_git_subcmd() {
  local fragment="$1"
  local after_git="${fragment#*git}"
  local saved_opts=$-
  set -f
  local skip_next=false subcmd=""
  for word in $after_git; do
    if $skip_next; then
      skip_next=false
      continue
    fi
    case "$word" in
      -C|-c|--git-dir|--work-tree|--namespace|--super-prefix|--config-env)
        skip_next=true ;;
      -*)
        ;;
      *)
        subcmd="$word"
        break ;;
    esac
  done
  # Restore the globbing state of our caller.
  if [[ "$saved_opts" != *f* ]]; then
    set +f
  fi
  printf '%s' "$subcmd"
}

# Split on shell operators so chained commands get inspected fragment by
# fragment. Replace operators with newlines, then walk the list.
FRAGMENTS=$(printf '%s' "$COMMAND" | sed -E 's/;/\n/g; s/&&/\n/g; s/\|\|/\n/g; s/\|/\n/g; s/\$\(/\n/g; s/`/\n/g')

while IFS= read -r fragment; do
  [ -z "$fragment" ] && continue
  if ! fragment_invokes_git "$fragment"; then
    continue
  fi

  subcmd=$(extract_git_subcmd "$fragment")
  if [ -z "$subcmd" ]; then
    emit_deny "Blocked by worktree-enforcement hook: could not determine the git subcommand in '$fragment'. This repo has opted into worktree discipline (.claude/worktree-required is committed). Run git write operations from inside a linked worktree — either change the session cwd into an existing worktree under .claude/worktrees/, use the EnterWorktree tool, or spawn an agent with isolation: worktree.$(cwd_anchor_note_if_chained "$COMMAND")$(git_C_note_if_present "$COMMAND")"
    exit 0
  fi

  if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
    emit_deny "Blocked by worktree-enforcement hook: 'git $subcmd' is not on the read-only allowlist, and this session is running in the main working tree of a repo that has opted into worktree discipline (.claude/worktree-required is committed). Run git write operations from inside a linked worktree — cd into an existing worktree under .claude/worktrees/, create one with 'git worktree add .claude/worktrees/<branch> -b <branch>' (that specific command is allowed on the main tree), or spawn an agent with isolation: worktree. See claude-config README 'Worktree enforcement' for details.$(cwd_anchor_note_if_chained "$COMMAND")$(git_C_note_if_present "$COMMAND")"
    exit 0
  fi
done <<< "$FRAGMENTS"

exit 0
