#!/bin/bash
# hook-class: informational
# UserPromptSubmit hook: report when the session is working from the main
# working tree of a repo that requires worktrees, while a linked worktree
# exists on disk.
#
# Audience: Claude (the agent), not humans. Output is a JSON payload with
# hookSpecificOutput.additionalContext — the harness injects it into the
# agent's conversation context. Mirrors check-branch-divergence.sh's
# envelope shape.
#
# What this catches: a session that never entered its worktree. Entering the
# worktree moves the working directory's reset target onto it, so a session
# that did enter self-corrects after a stray `cd`. A session that never
# entered has no such correction — every command, every dispatched subagent,
# and every review marker it writes describes the main checkout on the
# default branch instead of the branch the work belongs to.
#
# Advisory only: it never blocks and never re-anchors. Re-anchoring is a
# harness-level action the agent takes; a hook cannot change a session's
# working directory.
#
# Emits only when all three hold:
#   1. worktree enforcement is active for the resolved repo
#   2. the session's working directory is the repo's MAIN working tree
#   3. at least one linked worktree exists on disk
# Condition 3 keeps a solo main-tree repo (opted in, no worktree yet) quiet,
# which is the normal state right before `git worktree add`.
#
# Re-arming, not one-shot: anchor state is not monotonic — a session can
# enter a worktree and later drift back out. The per-session state file
# records the tree last reported, and is removed whenever the condition
# stops holding, so a genuine second occurrence is reported again. The
# one-shot `<session_id>` dedup used by the other nudge hooks would
# suppress it.
#
# STATE_DIR gets a 30-day eviction sweep at the point of report — reached
# only on a transition into the drifted state, not on every prompt, since
# the content-match dedup above re-arms and short-circuits everything else —
# no SessionEnd hook cleans these up.
#
# Exit 0 on every path. A non-zero exit from a UserPromptSubmit hook risks
# disrupting prompt submission, and every failure here (no jq, cwd outside a
# repo, detached HEAD, bare repo, unreadable state dir) is a reason to stay
# quiet rather than to interfere. Unlike require-worktree-for-git-writes.sh,
# unresolvable git state does NOT deny — that hook is a gate; this is an
# advisory.
#
# Every git call goes through _lib_capped, so a locked index or a dead
# network mount does not stall every prompt for the rest of the session in
# the common case. That bound carries the caveats _lib_capped's own doc
# comment in _lib.sh states: `timeout` sends SIGTERM, which a call blocked in
# uninterruptible I/O will not honor until it exits the syscall, and the
# no-`timeout`-binary fallback runs uncapped.

# Strict mode omitted deliberately, matching the other UserPromptSubmit
# nudges: this hook must reach `exit 0` on all paths, and `set -e` would
# turn an expected non-zero (a git call in a bare repo, a `grep` with no
# match) into an early exit that skips the exit-0 contract.

INPUT=$(cat 2>/dev/null)

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# Both fields in a single jq pass. Pre-initialized so a failed read (no jq,
# invalid INPUT) leaves empty strings rather than unbound variables.
SESSION_ID=""
CWD=""
{
  IFS= read -r SESSION_ID
  IFS= read -r CWD
} < <(
  printf '%s\n' "$INPUT" \
    | _lib_jq -r '(.session_id // ""),(.cwd // "")' \
    2>/dev/null
) 2>/dev/null || true

[ -z "$CWD" ] && CWD="$PWD"

# Without a session id there is nowhere to record "already reported", and an
# advisory that cannot dedup would repeat on every prompt for the rest of the
# session. Staying silent is the better failure. An id that isn't a safe
# single path component (e.g. containing "../") is treated the same way: it
# feeds STATE_FILE below, and this hook has no business trying to sanitize it
# further when staying silent is already the correct failure mode.
if [ -z "$SESSION_ID" ] || ! _lib_valid_session_id_component "$SESSION_ID"; then
  exit 0
fi

# Unresolvable config dir (empty/unset $HOME, no CLAUDE_CONFIG_DIR) stays
# quiet like every other degenerate input this advisory hook tolerates.
CONFIG_DIR=$(_lib_config_dir) || exit 0
STATE_DIR="$CONFIG_DIR/.worktree-anchor-nudge.d"
STATE_FILE="${STATE_DIR}/${SESSION_ID}"

# Clear the recorded report so the next occurrence is reported again.
_rearm() {
  rm -f "$STATE_FILE" 2>/dev/null || true
}

REPO_ROOT=$(_lib_capped git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  # Not in a git repo (or git unavailable): nothing to say, and no basis to
  # decide whether the previous report still stands. Leave the state file be.
  exit 0
fi

# Three-marker opt-in: repo sentinel, machine sentinel, per-repo opt-out. A
# repo that never opted in sees no change from this hook at all.
if ! _lib_worktree_enforcement_active "$REPO_ROOT"; then
  _rearm
  exit 0
fi

# "Am I in the main working tree?" For the main tree --absolute-git-dir and
# --git-common-dir return the same absolute path; for a linked worktree the
# former points at <common>/worktrees/<name>. Both come from one rev-parse
# call (git prints one line per query flag, in the order given).
SESSION_GIT_DIR=""
COMMON_GIT_DIR=""
{
  IFS= read -r SESSION_GIT_DIR
  IFS= read -r COMMON_GIT_DIR
} < <(_lib_capped git -C "$CWD" rev-parse --absolute-git-dir --path-format=absolute --git-common-dir 2>/dev/null) 2>/dev/null || true

if [ -z "$SESSION_GIT_DIR" ] || [ -z "$COMMON_GIT_DIR" ]; then
  # Unresolvable git state (bare repo, detached oddity, git failure). An
  # advisory has no business guessing here.
  exit 0
fi

if [ "$SESSION_GIT_DIR" != "$COMMON_GIT_DIR" ]; then
  # Already in a linked worktree — the anchored state this hook exists to ask for.
  _rearm
  exit 0
fi

# Does a linked worktree actually exist on disk? Shared with marker.sh's
# fail-closed check, so both features answer this question the same way.
LINKED_WORKTREE=$(_lib_first_live_linked_worktree "$REPO_ROOT" 2>/dev/null)

if [ -z "$LINKED_WORKTREE" ]; then
  # Opted in, but no worktree yet — the normal state just before
  # `git worktree add`. Nothing has gone wrong.
  _rearm
  exit 0
fi

# Condition holds. Report once per entry into this state.
PREVIOUSLY_REPORTED=$(cat "$STATE_FILE" 2>/dev/null)
if [ "$PREVIOUSLY_REPORTED" = "$REPO_ROOT" ]; then
  exit 0
fi

ADDITIONAL_CONTEXT=$(printf '%s\n%s\n%s' \
  "This session's working directory is the MAIN working tree ($REPO_ROOT) of a repo that requires worktrees, and a linked worktree already exists (e.g. $LINKED_WORKTREE)." \
  "Commands, dispatched subagents, and review markers will describe the main checkout on the default branch, not the branch the work belongs to — verification can pass against a tree nobody changed." \
  "Before further work, enter the worktree the task belongs to: EnterWorktree{path: \"<worktree path>\"}. See branch-management/SKILL.md § \"Anchor the session in the worktree\".")

mkdir -p "$STATE_DIR" 2>/dev/null || true
# Evict stale entries from one-shot runs that skipped SessionEnd cleanup.
# Reached only on a transition into the drifted state (the content-match
# dedup above gates it), not on every prompt.
if [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ]; then
  find "$STATE_DIR" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true
fi
printf '%s\n' "$REPO_ROOT" > "$STATE_FILE" 2>/dev/null || true

jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' || true
exit 0
