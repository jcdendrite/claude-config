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

Self-derives every fact the trust classification, the passive-execution
audit, and the checkout need rather than trusting them as arguments. First
checks that <owner>/<repo> matches this worktree's own origin remote,
aborting before any gh call on a mismatch. Then fetches this PR's own
author_association and cross-repo status directly from `gh api
repos/{owner}/{repo}/pulls/{number}` -- unconditionally, on every
invocation -- and refuses a FIRST_TIME_CONTRIBUTOR/NONE author or a
cross-repository PR (including a deleted-fork PR, whose head.repo reads
null) before any further fetch, naming review-pr-diff.sh as the path to use
instead. Only past that gate does it fetch the PR's own full, paginated
file list and its current headRefOid directly from `gh`, re-fetch
headRefOid once more to catch a force-push landing while the file list was
being paginated, pipe the file list to audit-execution-surface.py, and only
fetch refs/pull/<N>/head when the audit returns clean. A stop verdict exits
non-zero before any fetch of the PR's ref, naming the matched paths and
reasons on stderr. On a clean audit, asserts the fetched SHA still equals
the headRefOid this script itself fetched earlier in the same run -- a
force-push race between audit and checkout -- and aborts with no worktree
left behind on a mismatch. Also lists the fetched tree's own entries for the
PR's own changed files, for any git-tracked symlink (mode 120000) among
them, which audit-execution-surface.py's path-only match cannot see, and
aborts the same way on a hit. A pre-existing symlink elsewhere in the tree
that this PR does not touch is out of scope. A second run against the same
PR replaces the prior worktree. On success, rewrites this session's
provenance file with mode "checkout" (PR identity, the verified headRefOid,
this session's Claude PID, the mode) after this script's own independent
re-derivation -- never trusting review-pr-acquire.sh's own mode "acquired"
write. Prints the worktree's absolute path on stdout as the sole output of
a successful run.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

PR_IDENTITY="$1"

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

# Same split and validation review-pr-post.sh's own MARKER_PR_IDENTITY
# handling uses, so the two scripts agree on one PR-identity convention.
if ! PR_IDENTITY_FIELDS=$(_lib_parse_pr_identity "$PR_IDENTITY"); then
  echo "review-pr-checkout.sh: PR identity '$PR_IDENTITY' is not a valid <owner>/<repo>#<number>." >&2
  usage
  exit 2
fi
OWNER_REPO=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '1p')
PR_NUMBER=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '2p')

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
# Checked before the trust block below (a local misconfiguration, not a gh
# call) so an uninstalled skill aborts with no network round trip at all.
AUDIT_SCRIPT="$CONFIG_DIR/skills/review-pr/audit-execution-surface.py"
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "review-pr-checkout.sh: audit script not found at $AUDIT_SCRIPT -- the review-pr skill is not installed under this config dir. Abort before any fetch." >&2
  exit 2
fi

# Unconditional trust classification, enforced by the script rather than
# left to the model: a stop the model evaluates in prose is not a stop.
# This script already
# self-fetches everything else it refuses on, so the trust class belongs
# beside them, checked on every invocation -- placed before the headRefOid
# fetch and the paginated file-list call, so a refused PR never has its
# file list paginated.
# authorAssociation is not a `gh pr view --json` field (REFERENCES.md), so
# this REST call is the only way to get it; it also carries head.repo/
# base.repo, deriving cross-repo status independently of step 1's own
# isCrossRepository field rather than trusting that upstream read.
# Trust classification widens the stop conditions below; it never removes
# one -- a MEMBER/OWNER author paired with a cross-repository PR still
# refuses via the cross-repo check further down, regardless of standing.
GH_PR_TRUST_TIMEOUT_SECONDS=10
if ! TRUST_JSON=$(_lib_capped_for "$GH_PR_TRUST_TIMEOUT_SECONDS" env -u GH_HOST -u GH_ENTERPRISE_TOKEN gh api "repos/$OWNER_REPO/pulls/$PR_NUMBER" 2>/dev/null); then
  echo "review-pr-checkout.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's trust-classification data (gh api failed or timed out). Abort before any fetch -- a gh failure here must never be read as 'no restriction found'." >&2
  exit 2
fi
AUTHOR_ASSOCIATION=$(printf '%s' "$TRUST_JSON" | _lib_jq -r '.author_association // empty' 2>/dev/null) || AUTHOR_ASSOCIATION=""
if [[ -z "$AUTHOR_ASSOCIATION" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's trust-classification data carried no author_association (malformed response). Abort before any fetch." >&2
  exit 2
fi
# A null head.repo (the PR's fork was deleted) reads as empty here -- jq
# indexes a field off `null` as `null`, never an error -- so it falls
# through to the empty-HEAD_REPO_FULL_NAME branch below and is treated as
# cross-repo: failing toward the more restrictive path on an ambiguous read.
HEAD_REPO_FULL_NAME=$(printf '%s' "$TRUST_JSON" | _lib_jq -r '.head.repo.full_name // empty' 2>/dev/null) || HEAD_REPO_FULL_NAME=""
BASE_REPO_FULL_NAME=$(printf '%s' "$TRUST_JSON" | _lib_jq -r '.base.repo.full_name // empty' 2>/dev/null) || BASE_REPO_FULL_NAME=""
case "$AUTHOR_ASSOCIATION" in
  FIRST_TIME_CONTRIBUTOR | NONE)
    echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's author association is $AUTHOR_ASSOCIATION -- checkout is refused unconditionally for this trust class. Use ~/.claude/scripts/review-pr-diff.sh instead. Abort before any fetch." >&2
    exit 2
    ;;
esac
if [[ -z "$HEAD_REPO_FULL_NAME" || "$HEAD_REPO_FULL_NAME" != "$BASE_REPO_FULL_NAME" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER is cross-repository (head repo '$HEAD_REPO_FULL_NAME' vs base repo '$BASE_REPO_FULL_NAME') -- checkout is refused unconditionally for this trust class, regardless of author standing. Use ~/.claude/scripts/review-pr-diff.sh instead. Abort before any fetch." >&2
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

# _lib_main_repo_root, not $REPO_ROOT: review-pr-finish.sh reconstructs
# WORKTREE_DIR the same way, under the main tree, regardless of which tree
# this script itself is standing in -- both must derive WORKTREE_DIR
# identically or a checkout run from a linked worktree orphans its own
# worktree on cleanup. Every other use of $REPO_ROOT in this script (fetch,
# ls-tree, remote get-url) is unaffected: those correctly hit the shared
# object/ref/config store from either tree.
MAIN_REPO_ROOT=$(_lib_main_repo_root) || {
  echo "review-pr-checkout.sh: could not resolve this repository's main tree root. Abort with no worktree created." >&2
  exit 2
}
WORKTREE_DIR=$(_lib_review_pr_worktree_dir "$MAIN_REPO_ROOT" "$OWNER_REPO" "$PR_NUMBER")
# `git worktree add` (inside review-pr-worktree-replace.py below) creates
# WORKTREE_DIR's own leading directories itself, but the lock file lives
# alongside it and needs its own parent directory to exist beforehand, on
# this repo's very first review-pr run.
mkdir -p -- "$(dirname "$WORKTREE_DIR")"

# Resolved before the worktree-replace call below so SESSION_ID can be
# passed into review-pr-worktree-replace.py, which writes the
# WORKTREE_DIR.owner ownership sidecar itself, inside its own locked
# section -- atomic with the worktree creation itself. See that script's
# own header for why a caller-side write of that sidecar after the lock is
# released is unsafe.
SESSION_AND_PID=$(_lib_resolve_claude_pid) || {
  echo "review-pr-checkout.sh: could not resolve this session's id (capture-session-id.sh SessionStart hook did not run) -- cannot record provenance or ownership. Abort before creating a worktree." >&2
  exit 2
}
SESSION_ID="${SESSION_AND_PID%% *}"
CLAUDE_PID="${SESSION_AND_PID##* }"
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  echo "review-pr-checkout.sh: resolved session id '$SESSION_ID' is not a valid path component -- cannot record provenance or ownership. Abort before creating a worktree." >&2
  exit 2
fi

# Serializes concurrent invocations against the same PR through the whole
# remove/prune/add sequence, so one invocation's `worktree remove` can never
# delete a directory the other has already started reading from. Keyed to
# WORKTREE_DIR (unique per owner/repo/PR-number), so a concurrent run
# against a DIFFERENT PR never blocks on this one. review-pr-worktree-
# replace.py holds an fcntl.flock on WORKTREE_DIR.lock for the sequence's
# whole duration, which releases automatically on process exit (including
# SIGKILL), so a crashed holder is never observed as still holding it.
# 210s sizes the wait deadline to the locked section's own worst-case hold
# time under the lock, not to crash recovery: 7 git/rm calls x 30s
# (_review_pr_worktree.py's GIT_OP_TIMEOUT_SECONDS), covering the rollback
# path's second remove_worktree call alongside the base sequence's own. A
# waiter that times out is behind a legitimately slow, still-alive holder.
WORKTREE_REPLACE_SCRIPT="$(dirname "$0")/review-pr-worktree-replace.py"
WORKTREE_REPLACE_LOCK_WAIT_DEADLINE_SECONDS=210
WORKTREE_DIR_OUTPUT=$(python3 "$WORKTREE_REPLACE_SCRIPT" "$MAIN_REPO_ROOT" "$WORKTREE_DIR" "$FETCHED_SHA" "$SESSION_ID" "$WORKTREE_REPLACE_LOCK_WAIT_DEADLINE_SECONDS") || WORKTREE_DIR_OUTPUT=""
if [[ -z "$WORKTREE_DIR_OUTPUT" ]]; then
  echo "review-pr-checkout.sh: could not replace the review worktree at $WORKTREE_DIR (lock contention, a git worktree operation failed, or the ownership sidecar could not be written -- see review-pr-worktree-replace.py's own message above). Abort." >&2
  exit 2
fi

# Provenance write: rewrite this session's provenance file with mode
# "checkout" after this script's own independent re-derivation of PR
# identity and headRefOid -- never trusting
# review-pr-acquire.sh's own mode "acquired" write. marker.sh write
# review-pr reads this sibling file; an acquire-only session (mode still
# "acquired") can never write a completion marker.
PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)
mkdir -p -- "$(dirname "$PROVENANCE")"
if ! printf '%s\n%s\n%s\n%s\n' "$PR_IDENTITY" "$HEAD_REF_OID" "$CLAUDE_PID" "checkout" | _lib_write_no_follow "$PROVENANCE"; then
  echo "review-pr-checkout.sh: could not write provenance file $PROVENANCE -- cannot record this checkout. The worktree above was created; run ~/.claude/scripts/review-pr-finish.sh to clean it up. Abort." >&2
  exit 2
fi

printf '%s\n' "$WORKTREE_DIR_OUTPUT"
