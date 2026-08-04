#!/bin/bash
# hook-class: informational
# SessionStart hook: set the terminal tab title (hookSpecificOutput.sessionTitle)
# to `<repo>/<branch>` from git state, so a feature-branch or handoff-resumed
# session is distinguishable from every other tab without a manual /rename.
#
# Emits nothing (today's auto-titler runs unchanged) when: `.source` isn't
# "startup"; a kill switch is present (machine-global or per-repo); not a git
# repo; on the repo's default branch (the collision-prone case this hook
# exists to skip); the default branch is undeterminable (no `origin` remote,
# `origin/HEAD` unset, or `origin/HEAD` a dangling symref); the main worktree
# is bare; or either title component fails the character allowlist below.
# Every uncertain case fails closed to today's behavior, never to a guess.
#
# Detached HEAD titles as `<repo>/@<short-sha>` (git rev-parse --short HEAD,
# not `git describe` — describe walks the tag namespace and can run --dirty).
#
# Character allowlist: both components must match ^[A-Za-z0-9._/@+-]+$ under
# LC_ALL=C specifically. Outside the C locale, glibc's bracket-range matching
# is collation-order rather than codepoint, and admits accented/fullwidth
# characters (and disguises C1 control bytes 0x9b/0x9c, which terminate an
# OSC title string on a terminal decoding 8-bit C1) into [A-Za-z]. The pin,
# not the character class alone, is what makes this locale-independent.
#
# Repo component: basename of the first (always main, per git-worktree(1))
# record of `git worktree list --porcelain`, skipped if that record carries
# the `bare` attribute. A --separate-git-dir layout is not skipped (its
# record carries no `bare` attribute) — but on git 2.55.0 that record names
# the relocated git-dir, not the working directory, since `worktree list`
# has no administrative record of the main worktree's own path when it
# isn't colocated with the git-dir.
#
# Truncates the branch component to 32 chars (display heuristic for tab
# chrome, no vendor source) after the allowlist match, so an accepted value
# is guaranteed single-byte ASCII and truncation cannot cut mid-codepoint.
#
# Reads the input payload's top-level `.cwd` field, not process cwd —
# authoritative for a session anchored in a linked worktree.
#
# Kill switches: machine-global ~/.claude/.session-title-disabled (mirrors
# nudge-handoff-near-context-cap.sh's ~/.claude/.handoff-nudge-disabled);
# per-repo <main-worktree-root>/.claude/session-title-disabled, resolved
# against the main worktree root this hook already computes, never cwd.
#
# Known gap: `refs/remotes/origin/HEAD` is only ever written by `git clone`
# or `git remote set-head`; a repo built via `init` + `remote add` + push has
# no way to resolve a default branch, so this hook never fires there.
# Remedy: `git remote set-head origin -a`. A stale origin/HEAD (renamed
# default branch without `set-head -a`) resolves successfully to the wrong
# default and titles a default-branch session as `<repo>/<branch>` — same
# remedy.
#
# Not covered by design: resume/clear/compact/fork sources (a manual
# /rename must survive those); mid-session branch switches (title is set
# once, at SessionStart, and not refreshed).
#
# Verified against claude 2.1.220: hookSpecificOutput.sessionTitle sets the
# terminal tab title and is not overwritten by the auto-titler.
#
# Exit 0 always — this hook must not block session startup.

set -uo pipefail

# --- .source filter ------------------------------------------------------
# Read stdin the same way capture-session-id.sh does; do not use `read -t`,
# which is line-oriented and truncates JSON exceeding the pipe buffer. Do not
# rely on the settings.json matcher alone (CLAUDE.md, "Hook defense-in-depth"):
# /clear, /compact, --resume, and --fork-session must leave a manual /rename
# intact — check-branch-divergence.sh has no such internal filter, since a
# stale divergence advisory (its own failure mode) is harmless to re-emit.

INPUT=$(cat 2>/dev/null)
SOURCE=$(printf '%s' "$INPUT" | jq -r '.source // empty' 2>/dev/null)
[[ "$SOURCE" == "startup" ]] || exit 0

# --- machine-global kill switch ------------------------------------------

# An unresolvable config dir leaves no kill-switch location to check, so
# this hook fails open (today's auto-titler behavior) rather than guess.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi
CONFIG_DIR=$(_lib_config_dir) || exit 0
[ -f "$CONFIG_DIR/.session-title-disabled" ] && exit 0

# --- authoritative cwd -----------------------------------------------------
# Run git against the payload's .cwd, not process cwd: a linked-worktree
# session whose payload .cwd differs from process cwd must title from the
# worktree's branch.

PAYLOAD_CWD=$(printf '%s' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$PAYLOAD_CWD" ] && exit 0

# Known limitation: unlike check-branch-divergence.sh's one true network
# call, none of the git invocations below are timeout-wrapped — they're
# local plumbing reads, fast under any normal filesystem. A hung/stale
# network-mounted $PAYLOAD_CWD could still block one of them past "exit 0
# always," but a session whose own cwd is unreachable is already failing
# broadly elsewhere; adding a timeout wrapper here for that one case would
# out-scale the problem it defends against.
git -C "$PAYLOAD_CWD" rev-parse --git-dir >/dev/null 2>&1 || exit 0

# --- branch component ------------------------------------------------------
# symbolic-ref -q --short HEAD is empty on detached HEAD; the default-branch
# gate below only applies when HEAD is a real branch. This ordering means a
# repo with both an undeterminable default branch and a detached HEAD always
# takes the detached-HEAD path — deterministic, not a race.

CURRENT_BRANCH=$(git -C "$PAYLOAD_CWD" symbolic-ref -q --short HEAD 2>/dev/null)
if [ -n "$CURRENT_BRANCH" ]; then
  DEFAULT_REF=$(git -C "$PAYLOAD_CWD" symbolic-ref -q refs/remotes/origin/HEAD 2>/dev/null)
  [ -z "$DEFAULT_REF" ] && exit 0
  # A dangling origin/HEAD symref still resolves via symbolic-ref -q (it just
  # reads the pointer text) but must not be conflated with a live default
  # branch — verify the target actually resolves to a commit.
  git -C "$PAYLOAD_CWD" rev-parse --verify --quiet "$DEFAULT_REF" >/dev/null 2>&1 || exit 0
  DEFAULT_BRANCH=${DEFAULT_REF#refs/remotes/origin/}
  [ -z "$DEFAULT_BRANCH" ] && exit 0
  [ "$CURRENT_BRANCH" = "$DEFAULT_BRANCH" ] && exit 0
  BRANCH_COMPONENT="$CURRENT_BRANCH"
else
  SHORT_SHA=$(git -C "$PAYLOAD_CWD" rev-parse --short HEAD 2>/dev/null)
  [ -z "$SHORT_SHA" ] && exit 0
  BRANCH_COMPONENT="@$SHORT_SHA"
fi

# --- repo component ---------------------------------------------------------
# The first `worktree` record is always the main worktree (git-worktree(1)).
# core.quotePath=false is belt-and-braces, not load-bearing — the allowlist
# below rejects a non-ASCII path whether or not it arrives C-quoted.

WORKTREE_PORCELAIN=$(git -C "$PAYLOAD_CWD" -c core.quotePath=false worktree list --porcelain 2>/dev/null)
[ -z "$WORKTREE_PORCELAIN" ] && exit 0
# LC_ALL=C on every parse below: the branch name embedded in this output can
# carry the same non-UTF8 bytes the allowlist exists to catch, and a
# locale-aware awk/grep attempting multibyte validation on those bytes can
# warn or misbehave — C-locale processing treats the text as raw bytes.
MAIN_WORKTREE_RECORD=$(printf '%s\n' "$WORKTREE_PORCELAIN" | LC_ALL=C awk '/^$/{exit} {print}')
MAIN_WORKTREE_ROOT=$(printf '%s\n' "$MAIN_WORKTREE_RECORD" | LC_ALL=C awk '/^worktree /{print substr($0, 10); exit}')
[ -z "$MAIN_WORKTREE_ROOT" ] && exit 0
printf '%s\n' "$MAIN_WORKTREE_RECORD" | LC_ALL=C grep -qx 'bare' && exit 0

# --- per-repo kill switch --------------------------------------------------
# Resolved against the main worktree root, never cwd: an untracked sentinel
# in the main checkout is invisible from a linked worktree, and a
# cwd-relative lookup also misses when the session launches from a
# subdirectory.

[ -f "$MAIN_WORKTREE_ROOT/.claude/session-title-disabled" ] && exit 0

REPO_COMPONENT=$(basename "$MAIN_WORKTREE_ROOT")

# --- character allowlist ---------------------------------------------------

ALLOWLIST_RE='^[A-Za-z0-9._/@+-]+$'
printf '%s' "$REPO_COMPONENT" | LC_ALL=C grep -Eq "$ALLOWLIST_RE" || exit 0
printf '%s' "$BRANCH_COMPONENT" | LC_ALL=C grep -Eq "$ALLOWLIST_RE" || exit 0

BRANCH_COMPONENT="${BRANCH_COMPONENT:0:32}"

TITLE="${REPO_COMPONENT}/${BRANCH_COMPONENT}"
jq -n --arg title "$TITLE" \
  '{hookSpecificOutput: {hookEventName: "SessionStart", sessionTitle: $title}}' || true
exit 0
