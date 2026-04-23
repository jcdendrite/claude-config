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

# Fail-closed on malformed input: if jq couldn't parse the stdin JSON, we
# can't tell what Claude is about to run, so deny rather than silently allow.
if [ "$JQ_EXIT" -ne 0 ]; then
  emit_deny "Blocked by worktree-enforcement hook: could not parse tool-input JSON. Refusing to evaluate git discipline under malformed input."
  exit 0
fi

# Fast-path: commands that don't mention git are not our concern. Use bash
# pattern matching instead of grep to avoid locale-dependent behavior on
# commands containing non-UTF-8 bytes.
if [[ "$COMMAND" != *git* ]]; then
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
  verify-commit
  verify-tag
  version
  worktree        # bootstrap for this whole mechanism — don't block it
)
ALLOWED_RE=$(IFS='|'; echo "${ALLOWED_SUBCMDS[*]}")

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
  if [[ "$fragment" != *git* ]]; then
    continue
  fi

  subcmd=$(extract_git_subcmd "$fragment")
  if [ -z "$subcmd" ]; then
    emit_deny "Blocked by worktree-enforcement hook: could not determine the git subcommand in '$fragment'. This repo has opted into worktree discipline (.claude/worktree-required is committed). Run git write operations from inside a linked worktree — either change the session cwd into an existing worktree under .claude/worktrees/, use the EnterWorktree tool, or spawn an agent with isolation: worktree."
    exit 0
  fi

  if ! [[ "$subcmd" =~ ^($ALLOWED_RE)$ ]]; then
    emit_deny "Blocked by worktree-enforcement hook: 'git $subcmd' is not on the read-only allowlist, and this session is running in the main working tree of a repo that has opted into worktree discipline (.claude/worktree-required is committed). Run git write operations from inside a linked worktree — cd into an existing worktree under .claude/worktrees/, create one with 'git worktree add .claude/worktrees/<branch> -b <branch>' (that specific command is allowed on the main tree), or spawn an agent with isolation: worktree. See claude-config README 'Worktree enforcement' for details."
    exit 0
  fi
done <<< "$FRAGMENTS"

exit 0
