#!/usr/bin/env bash
# Audit-then-checkout for /review-pr Step 2, made unbypassable by
# construction. This script re-derives the PR's own repo identity, file
# list, and headRefOid itself from `gh`/git, never trusting Step 1's own
# read or a value passed in as an argument -- the same self-verifying
# pattern review-pr-post.sh already uses for the post step. A PreToolUse
# hook can only confirm that *some* audit ran, not that its input went
# untampered: the same prompt injection this audit guards against could
# just as easily instruct the agent to "audit an empty list" before such a
# hook's own check, or to invoke this very script against a different,
# attacker-controlled repo than the one actually being checked out. So
# nothing upstream -- including a compromised Step 1 read, or the $1
# argument's own repo identity -- can hand this script a doctored file
# list or a mismatched repo.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-checkout.sh <owner>/<repo>#<number>

Self-derives every fact the passive-execution audit and the checkout need
rather than trusting them as arguments. First checks that <owner>/<repo>
matches this worktree's own origin remote, aborting before any gh call on a
mismatch. Then fetches the PR's own full, paginated file list and its
current headRefOid directly from `gh`, re-fetches headRefOid once more to
catch a force-push landing while the file list was being paginated, pipes
the file list to audit-execution-surface.py, and only fetches
refs/pull/<N>/head when the audit returns clean. A stop verdict exits
non-zero before any fetch of the PR's ref, naming the matched paths and
reasons on stderr. On a clean audit, asserts the fetched SHA still equals
the headRefOid this script itself fetched earlier in the same run -- a
force-push race between audit and checkout -- and aborts with no worktree
left behind on a mismatch. Also lists the fetched tree's own entries for the
PR's own changed files, for any git-tracked symlink (mode 120000) among
them, which audit-execution-surface.py's path-only match cannot see, and
aborts the same way on a hit. A pre-existing symlink elsewhere in the tree
that this PR does not touch is out of scope. A second run against the same
PR replaces the prior worktree. Prints the worktree's absolute path on
stdout as the sole output of a successful run.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

PR_IDENTITY="$1"
# Same <owner>/<repo>#<number> shape and split as review-pr-post.sh's own
# MARKER_PR_IDENTITY handling, so the two scripts agree on one PR-identity
# convention.
PR_NUMBER="${PR_IDENTITY##*#}"
OWNER_REPO="${PR_IDENTITY%#*}"
if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "review-pr-checkout.sh: PR identity '$PR_IDENTITY' has no numeric PR number." >&2
  usage
  exit 2
fi
# Each segment must hold at least one alphanumeric character -- a bare `..`
# or `.` segment passes a naive [A-Za-z0-9._-]+ class (it's a valid,
# nonempty run of allowed characters) and turns a `repos/$OWNER_REPO/...`
# gh api interpolation into a path-traversal shape (e.g. `../..#5` yields
# `repos/../../pulls/5/files`).
if [[ ! "$OWNER_REPO" =~ ^[A-Za-z0-9._-]*[A-Za-z0-9][A-Za-z0-9._-]*/[A-Za-z0-9._-]*[A-Za-z0-9][A-Za-z0-9._-]*$ ]]; then
  echo "review-pr-checkout.sh: PR identity '$PR_IDENTITY' does not name a valid owner/repo." >&2
  usage
  exit 2
fi

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-checkout.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty). Abort before any fetch." >&2
  exit 2
}

REPO_ROOT=$(_lib_capped git rev-parse --show-toplevel 2>/dev/null) || REPO_ROOT=""
if [[ -z "$REPO_ROOT" ]]; then
  echo "review-pr-checkout.sh: not inside a git repository. Abort before any fetch." >&2
  exit 2
fi

# Self-derive the repo identity too, the same way headRefOid and the file
# list are self-derived below -- OWNER_REPO above is parsed from $1 alone,
# never cross-checked against anything this script controls. A PR's
# headRefOid is content-addressed: an attacker can push the real PR's own
# head commit to a second, fully-attacker-controlled repo against a decoy
# base, so that decoy reports the same headRefOid this script fetches for
# the real PR while its own diff is empty or trivial. The ref fetch further
# below always targets THIS worktree's origin regardless of $OWNER_REPO, so
# an unchecked mismatch would let the audit run against the decoy's
# manufactured file list while the checkout still lands the real PR's
# unaudited tree -- the headRefOid equality check later in this script
# can't catch that, since both sides legitimately agree. Comparing
# $OWNER_REPO against origin here, before either gh call, closes it.
ORIGIN_URL=$(_lib_capped git -C "$REPO_ROOT" remote get-url origin 2>/dev/null) || ORIGIN_URL=""
if [[ -z "$ORIGIN_URL" ]]; then
  echo "review-pr-checkout.sh: could not resolve this worktree's origin remote. Abort before any fetch." >&2
  exit 2
fi
# Same owner/repo extraction require-respond-pr.sh's own cross-repo check
# uses, so both sides parse an origin URL identically: the last two
# ':'- or '/'-delimited path segments, trailing '.git' stripped.
ORIGIN_OWNER_REPO=$(printf '%s\n' "$ORIGIN_URL" | sed -nE 's|.*[:/]([^/:]+/[^/]+)$|\1|p' | sed 's|\.git$||')
if [[ -z "$ORIGIN_OWNER_REPO" ]]; then
  echo "review-pr-checkout.sh: could not parse an owner/repo out of this worktree's origin remote URL '$ORIGIN_URL'. Abort before any fetch." >&2
  exit 2
fi
if [[ "$ORIGIN_OWNER_REPO" != "$OWNER_REPO" ]]; then
  echo "review-pr-checkout.sh: PR identity '$PR_IDENTITY' names repo '$OWNER_REPO', which does not match this worktree's own origin remote ('$ORIGIN_OWNER_REPO'). Abort before any fetch -- see this script's header comment for the cross-repo substitution this check exists to close." >&2
  exit 2
fi

# claude-skills/skills/review-pr/ stows to $CONFIG_DIR/skills/review-pr/
# (stow-packages.sh) -- the same installed path SKILL.md's own Step 2 names
# literally. Anchoring on $CONFIG_DIR rather than a relative ../../.. guess
# off this script's own path survives a future scripts/ directory move.
AUDIT_SCRIPT="$CONFIG_DIR/skills/review-pr/audit-execution-surface.py"
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "review-pr-checkout.sh: audit script not found at $AUDIT_SCRIPT -- the review-pr skill is not installed under this config dir. Abort before any fetch." >&2
  exit 2
fi

# GH_HOST/GH_ENTERPRISE_TOKEN stripped from every gh call below, same
# reasoning as review-pr-post.sh's own calls: adversarial PR content could
# induce the calling agent to set GH_HOST ambiently, silently redirecting a
# fact this script is supposed to be deriving independently (the file list,
# the headRefOid) to an attacker-chosen host.

# 10s: a network GET carrying no payload, the same budget review-pr-post.sh
# uses for its own gh pr view identity re-fetch.
GH_PR_VIEW_TIMEOUT_SECONDS=10
HEAD_REF_OID=$(_lib_capped_for "$GH_PR_VIEW_TIMEOUT_SECONDS" env -u GH_HOST -u GH_ENTERPRISE_TOKEN gh pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || HEAD_REF_OID=""
if [[ -z "$HEAD_REF_OID" ]]; then
  echo "review-pr-checkout.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's current headRefOid. Abort before any fetch of the PR's ref." >&2
  exit 2
fi

# 30s, not the 10s above: this walks every page of the files listing via
# --paginate, which can be several round trips for a large PR, unlike the
# single-GET headRefOid call above.
GH_PR_FILES_TIMEOUT_SECONDS=30
# The REST files endpoint with --paginate, not `gh pr view --json files`,
# which silently caps at 100 entries with no --paginate equivalent
# (REFERENCES.md) -- this is exactly the full, paginated list the audit
# below must see, self-fetched rather than trusted from Step 1's own read.
if ! RAW_FILES=$(_lib_capped_for "$GH_PR_FILES_TIMEOUT_SECONDS" env -u GH_HOST -u GH_ENTERPRISE_TOKEN gh api "repos/$OWNER_REPO/pulls/$PR_NUMBER/files" --paginate --jq '.[].filename' 2>/dev/null); then
  echo "review-pr-checkout.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's file list (gh api --paginate failed or timed out). Abort before any fetch of the PR's ref -- a partial or failed listing must never be audited as if it were the full, or an empty, file set." >&2
  exit 2
fi

# Converts the newline-delimited filenames above into the JSON array
# audit-execution-surface.py's stdin contract requires. Kept as its own
# checked step, separate from the gh fetch above, so a failure here reports
# "could not encode as JSON" rather than being folded into the gh fetch's
# own "could not fetch" message -- `pipefail` (set at the top of this
# script) already makes a combined pipeline's exit status the correct
# rightmost-nonzero value, so this split is for error-message precision, not
# to work around a pipefail gap.
if ! FILES_JSON=$(printf '%s' "$RAW_FILES" | _lib_jq -R -s 'split("\n") | map(select(length > 0))' 2>/dev/null); then
  echo "review-pr-checkout.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's file list as JSON. Abort before any fetch of the PR's ref." >&2
  exit 2
fi

# TOCTOU guard: HEAD_REF_OID above was fetched before the file list just
# above it, so a force-push landing in that window would let the audit run
# against a file list that no longer matches the PR's current head -- the
# final checkout's own HEAD_REF_OID comparison further below only re-verifies
# at the fetch/checkout boundary, never at the moment this file list was
# captured. Re-fetch headRefOid here and compare against the value captured
# above, before the audit runs against a possibly-stale list.
HEAD_REF_OID_RECHECK=$(_lib_capped_for "$GH_PR_VIEW_TIMEOUT_SECONDS" env -u GH_HOST -u GH_ENTERPRISE_TOKEN gh pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || HEAD_REF_OID_RECHECK=""
if [[ -z "$HEAD_REF_OID_RECHECK" ]]; then
  echo "review-pr-checkout.sh: could not re-fetch PR $OWNER_REPO#$PR_NUMBER's headRefOid to confirm the file list above is still current. Abort before any fetch of the PR's ref." >&2
  exit 2
fi
if [[ "$HEAD_REF_OID_RECHECK" != "$HEAD_REF_OID" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's headRefOid changed from $HEAD_REF_OID to $HEAD_REF_OID_RECHECK while its file list was being fetched -- a force-push race between the two self-fetches. Abort before any fetch of the PR's ref." >&2
  exit 2
fi

# The audit's own exit code mirrors its "stop" verdict (1 = stop, 0 = clean,
# 2 = malformed stdin). Captured explicitly via the if/else exemption from
# `set -e` (shell-script-conventions.md) rather than a bare pipeline, so the
# stop path can still print the audit's own named matches before exiting.
if AUDIT_OUTPUT=$(printf '%s' "$FILES_JSON" | python3 "$AUDIT_SCRIPT" 2>/dev/null); then
  AUDIT_EXIT=0
else
  AUDIT_EXIT=$?
fi

if [[ "$AUDIT_EXIT" -eq 2 ]]; then
  echo "review-pr-checkout.sh: audit-execution-surface.py rejected its own self-fetched file list as malformed input: $AUDIT_OUTPUT" >&2
  exit 2
fi
if [[ "$AUDIT_EXIT" -ne 0 ]]; then
  MATCHES=$(printf '%s' "$AUDIT_OUTPUT" | _lib_jq -r '.matches[] | "\(.path): \(.reason)"' 2>/dev/null) || MATCHES="$AUDIT_OUTPUT"
  echo "review-pr-checkout.sh: passive-execution audit stopped PR $OWNER_REPO#$PR_NUMBER before checkout -- no refs/pull/$PR_NUMBER/head fetch was made. Matched paths:" >&2
  printf '%s\n' "$MATCHES" >&2
  exit 2
fi

# Local ref namespace scoped to this script, distinct from any branch name a
# contributor might already have locally, so this fetch can never collide
# with or overwrite an unrelated ref.
LOCAL_REF="refs/review-pr/pr-$PR_NUMBER"
# 30s, matching the files-listing budget above: a network fetch, not a local
# read.
GH_FETCH_TIMEOUT_SECONDS=30
if ! _lib_capped_for "$GH_FETCH_TIMEOUT_SECONDS" git -C "$REPO_ROOT" fetch origin "refs/pull/$PR_NUMBER/head:$LOCAL_REF" --force >/dev/null 2>&1; then
  echo "review-pr-checkout.sh: could not fetch refs/pull/$PR_NUMBER/head for PR $OWNER_REPO#$PR_NUMBER from origin. Abort." >&2
  exit 2
fi

FETCHED_SHA=$(_lib_capped git -C "$REPO_ROOT" rev-parse "$LOCAL_REF" 2>/dev/null) || FETCHED_SHA=""
if [[ -z "$FETCHED_SHA" ]]; then
  echo "review-pr-checkout.sh: fetched ref $LOCAL_REF did not resolve to a commit. Abort." >&2
  exit 2
fi

# Compared against the headRefOid THIS SCRIPT fetched above, never a value
# passed in -- a mismatch means a force-push landed between the audit's
# fetch and this checkout's fetch, so the tree about to be checked out is
# not the tree the audit actually saw.
if [[ "$FETCHED_SHA" != "$HEAD_REF_OID" ]]; then
  echo "review-pr-checkout.sh: fetched refs/pull/$PR_NUMBER/head ($FETCHED_SHA) does not match PR $OWNER_REPO#$PR_NUMBER's headRefOid ($HEAD_REF_OID) fetched moments ago -- a force-push race between audit and checkout. Abort with no worktree created." >&2
  exit 2
fi

# audit-execution-surface.py classifies by path text alone, so it is blind
# to a git-tracked symlink (tree-entry mode 120000, vs 100644/100755 for a
# regular file) -- REFERENCES.md's "Git-tracked symlinks" section names this
# as a separate mechanism from _classify()'s path-only match. An
# innocuously-named symlink (e.g. notes.txt -> an absolute path into the
# operator's home directory holding local credentials)
# checks out verbatim via `git worktree add` with no target validation, and
# a Read tool then transparently returns the target's content. Checked here,
# against $FETCHED_SHA's own tree -- only resolvable locally now that the ref
# fetch above has landed those objects -- and before any worktree exposes
# them to a Read-driven review step.
#
# Scoped to the PR's own changed files, not the whole tree: a symlink
# already committed on the base branch that this PR never touches is not
# this PR's own risk, and must not stop every future review of the repo.
# CHANGED_FILE_PATHS is the same newline-delimited list RAW_FILES already
# holds for the path-based audit above -- built with a `read` loop, not
# `mapfile`/`readarray` (bash-4+, forbidden here; test_no_bash4_constructs.py).
CHANGED_FILE_PATHS=()
while IFS= read -r changed_path; do
  [[ -n "$changed_path" ]] && CHANGED_FILE_PATHS+=("$changed_path")
done <<< "$RAW_FILES"

# 30s, not _lib_capped's 5s local-read default: `ls-tree -r` still walks a
# subtree per pathspec given, whose cost scales with the number and depth of
# the PR's own changed paths, not with a single local index read.
SYMLINK_CHECK_TIMEOUT_SECONDS=30
SYMLINK_ENTRIES=""
if [[ "${#CHANGED_FILE_PATHS[@]}" -gt 0 ]]; then
  # --literal-pathspecs: a changed-file path is untrusted PR content, so a
  # filename containing pathspec magic characters (e.g. a leading `:`) must
  # never be reinterpreted as a glob or magic pathspec instead of matched
  # literally.
  if ! SYMLINK_ENTRIES=$(_lib_capped_for "$SYMLINK_CHECK_TIMEOUT_SECONDS" git -C "$REPO_ROOT" --literal-pathspecs ls-tree -r "$FETCHED_SHA" -- "${CHANGED_FILE_PATHS[@]}" 2>/dev/null \
    | awk -F'\t' '{ if (substr($1, 1, 6) == "120000") print $2 }'); then
    echo "review-pr-checkout.sh: could not list PR $OWNER_REPO#$PR_NUMBER's changed-file tree entries at $FETCHED_SHA to check for git-tracked symlinks. Abort with no worktree created." >&2
    exit 2
  fi
fi
if [[ -n "$SYMLINK_ENTRIES" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER tracks a git symlink -- git checks it out verbatim with no target validation, and a Read tool could transparently follow it outside the repo. Abort with no worktree created. Matched paths:" >&2
  printf '%s\n' "$SYMLINK_ENTRIES" >&2
  exit 2
fi

WORKTREE_DIR="$REPO_ROOT/.claude/worktrees/review-pr-${OWNER_REPO//\//-}-$PR_NUMBER"
# `git worktree add` below creates WORKTREE_DIR's own leading directories
# itself, but the lock directory below is a plain `mkdir` (not `mkdir -p`),
# so its parent must already exist on this repo's very first review-pr run.
mkdir -p -- "$(dirname "$WORKTREE_DIR")"

# Serializes concurrent invocations against the same PR through the whole
# check/remove/add sequence below, so one invocation's `worktree remove`
# can never delete a directory the other has already started reading from.
# Keyed to WORKTREE_DIR (unique per owner/repo/PR-number), so a concurrent
# run against a DIFFERENT PR never blocks on this one. Directory-mutex,
# published via the atomic rename below -- rather than flock(1), which
# stock macOS does not ship.
# LOCK_WAIT_DEADLINE_SECONDS bounds how long a second invocation blocks
# before giving up, rather than waiting forever behind a lock a crashed
# prior run never released.
LOCK_DIR="$WORKTREE_DIR.lock"
# Overridable for tests exercising the deadline-exceeded path without a real
# 30s wait; malformed (empty, non-digit, zero, zero-padded, or 9+ digits)
# falls back to the production default, same guard shape as marker.sh's
# CODE_REVIEW_CHECK_MAX_AGE_SECONDS.
case "${REVIEW_PR_LOCK_WAIT_DEADLINE_SECONDS:-}" in
  ''|0|*[!0-9]*|0[0-9]*|?????????*) LOCK_WAIT_DEADLINE_SECONDS=30 ;;
  *) LOCK_WAIT_DEADLINE_SECONDS="$REVIEW_PR_LOCK_WAIT_DEADLINE_SECONDS" ;;
esac
# A lock whose owner PID is dead, or whose owner file has aged past this many
# minutes, is reclaimed rather than waited out: this script's own internal
# timeouts (two headRefOid fetches, the paginated files fetch, the ref
# fetch, the symlink ls-tree, and up to two worktree ops) sum to under 170s
# in the worst case, so a lock still held after 5 minutes almost certainly
# outlived a SIGKILLed prior run, not a slow-but-alive one. Overridable for
# tests exercising the aged-live-PID reclaim path without a real 5-minute
# wait; same malformed-value-fallback guard shape as
# LOCK_WAIT_DEADLINE_SECONDS above.
case "${REVIEW_PR_LOCK_STALE_AGE_MINUTES:-}" in
  ''|0|*[!0-9]*|0[0-9]*|?????????*) LOCK_STALE_AGE_MINUTES=5 ;;
  *) LOCK_STALE_AGE_MINUTES="$REVIEW_PR_LOCK_STALE_AGE_MINUTES" ;;
esac
LOCK_DEADLINE=$(( $(date +%s) + LOCK_WAIT_DEADLINE_SECONDS ))
LOCK_ACQUIRED=""
until [[ -n "$LOCK_ACQUIRED" ]]; do
  # Atomic acquisition: stage the owner-PID file inside a mktemp -d sibling
  # (same parent directory as LOCK_DIR, so the publish step below stays on
  # one filesystem), then publish it onto LOCK_DIR's own path via a single
  # os.rename(2) call. rename(2) fails outright (ENOTEMPTY) when LOCK_DIR
  # already exists and is non-empty, unlike a plain `mv` onto an existing
  # directory, which would nest the staged directory inside it instead of
  # failing. A bare `mkdir "$LOCK_DIR"` followed by a separate `printf >
  # owner` (the prior shape here) left a window where a waiter could read
  # an empty or missing owner file and misjudge a lock that is
  # mid-acquisition as orphaned.
  STAGED_LOCK_DIR=$(mktemp -d "$WORKTREE_DIR.lock.XXXXXX" 2>/dev/null) || STAGED_LOCK_DIR=""
  if [[ -n "$STAGED_LOCK_DIR" ]]; then
    printf '%s' "$$" > "$STAGED_LOCK_DIR/owner"
    if _lib_capped python3 -c '
import os, sys
try:
    os.rename(sys.argv[1], sys.argv[2])
except OSError:
    sys.exit(1)
' "$STAGED_LOCK_DIR" "$LOCK_DIR" 2>/dev/null; then
      LOCK_ACQUIRED=1
    else
      rm -rf -- "$STAGED_LOCK_DIR" 2>/dev/null
    fi
  fi
  if [[ -z "$LOCK_ACQUIRED" ]]; then
    # Same two-part liveness test _lib_active_bypass_marker_live applies to
    # its own markers (dead PID, or a live PID whose marker has aged out) --
    # not that function itself, since a directory-based mutex with a PID
    # file inside it is a different marker shape than its single-file
    # session markers.
    LOCK_OWNER_PID=$(_lib_capped cat "$LOCK_DIR/owner" 2>/dev/null | _lib_capped tr -d '[:space:]') || LOCK_OWNER_PID=""
    if ! { [[ "$LOCK_OWNER_PID" =~ ^[0-9]+$ ]] && kill -0 "$LOCK_OWNER_PID" 2>/dev/null \
      && [[ -n "$(_lib_capped find "$LOCK_DIR/owner" -mmin -"$LOCK_STALE_AGE_MINUTES" 2>/dev/null)" ]]; }; then
      # Dead PID, or aged past the ceiling above -- reclaim rather than wait
      # out the full deadline. Falls through to the deadline check and sleep
      # below rather than looping back immediately: an `rm -rf` that keeps
      # failing (e.g. a permissions issue) must still hit the deadline
      # instead of spinning with no sleep.
      rm -rf -- "$LOCK_DIR" 2>/dev/null
    fi
    if [[ "$(date +%s)" -ge "$LOCK_DEADLINE" ]]; then
      echo "review-pr-checkout.sh: could not acquire the worktree lock at $LOCK_DIR within ${LOCK_WAIT_DEADLINE_SECONDS}s -- a concurrent invocation against the same PR may still be running. If it is not, remove the lock with: rmdir $LOCK_DIR. Abort." >&2
      exit 2
    fi
    sleep 0.2
  fi
done
# Re-reads $LOCK_DIR/owner rather than trusting this process still owns it:
# a lock robbed by a waiter that misjudged it stale (a narrow race this
# process cannot itself prevent) must never have its rightful new owner's
# live lock torn down by this process's own stale cleanup.
trap '[[ "$(_lib_capped cat "$LOCK_DIR/owner" 2>/dev/null)" == "$$" ]] && { rm -f "$LOCK_DIR/owner" 2>/dev/null; rmdir "$LOCK_DIR" 2>/dev/null; }' EXIT

# A second run against the same PR replaces the prior worktree rather than
# erroring on an existing path (SKILL.md's own rerun policy). `worktree
# remove --force` alone can fail on a not-quite-clean prior worktree, so the
# directory removal plus a prune is the fallback rather than leaving a
# half-torn-down worktree behind.
#
# 30s, matching this script's own network-fetch budget above: a checkout/
# teardown's cost scales with tree size, the same as a fetch, not with a
# local index read (_lib_capped's 5s default).
WORKTREE_OP_TIMEOUT_SECONDS=30
if [[ -e "$WORKTREE_DIR" ]]; then
  if ! _lib_capped_for "$WORKTREE_OP_TIMEOUT_SECONDS" git -C "$REPO_ROOT" worktree remove --force "$WORKTREE_DIR" >/dev/null 2>&1; then
    rm -rf -- "$WORKTREE_DIR"
  fi
fi
# Unconditional, not only inside the `[[ -e ]]` branch above: per
# git-worktree(1), a prior run's directory can be removed (by this script,
# or by hand) while its .git/worktrees/<id> metadata survives, which makes
# `[[ -e "$WORKTREE_DIR" ]]` false and would otherwise skip the prune that
# clears it -- the subsequent un-forced `worktree add` then fails
# deterministically against that stale metadata.
_lib_capped git -C "$REPO_ROOT" worktree prune >/dev/null 2>&1 || true

# --detach: no branch name, so this worktree is invisible to
# cleanup-idle-open-pr-worktrees.sh, which classifies reclaim candidates by
# matching a branch name to an open PR. Accepted gap -- a dedicated reclaim
# mechanism for detached review-pr worktrees is a larger follow-up, out of
# scope here.
if ! _lib_capped_for "$WORKTREE_OP_TIMEOUT_SECONDS" git -C "$REPO_ROOT" worktree add --detach "$WORKTREE_DIR" "$FETCHED_SHA" >/dev/null 2>&1; then
  echo "review-pr-checkout.sh: git worktree add failed for $WORKTREE_DIR at $FETCHED_SHA. Abort." >&2
  exit 2
fi

printf '%s\n' "$WORKTREE_DIR"
