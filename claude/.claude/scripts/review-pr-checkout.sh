#!/usr/bin/env bash
# Audit-then-checkout for /review-pr Step 2.
# This script re-derives the PR's repo identity, file list, and headRefOid itself
# from `gh`/git, never trusting Step 1's read or a value passed in as an argument.
# review-pr-post.sh self-verifies the same way for the post step.
# A PreToolUse hook can only confirm that some audit ran, not what it read.
# The threat is an injected instruction to audit an empty file list, or to run
# this script against an attacker-controlled decoy repo.
# So no upstream value -- a compromised Step 1 read, or $1's repo identity -- can
# hand this script a doctored file list or a mismatched repo.
set -euo pipefail

# Distinct from the exit 2 every operational failure uses, so a caller can
# tell "this PR needs review-pr-diff.sh" apart from "something broke".
# Covers every refusal that names review-pr-diff.sh as the alternative, and
# only a positive verdict: a tool that failed to produce one exits 2.
EXIT_CHECKOUT_REFUSED=3

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-checkout.sh <owner>/<repo>#<number>

Self-derives every fact the trust classification, the passive-execution
audit, and the checkout need rather than trusting them as arguments. In order:
1. Checks that <owner>/<repo> matches this worktree's own origin remote,
   aborting before any gh call on a mismatch.
2. Fetches this PR's own author_association, cross-repo status, base branch,
   and the base repository's default branch from
   `gh api repos/{owner}/{repo}/pulls/{number}`, on every invocation. Refuses
   an author whose association is not one of MEMBER, OWNER, COLLABORATOR, or
   CONTRIBUTOR, and a cross-repository PR (including a deleted-fork PR, whose
   head.repo reads null), before any further fetch. Every refusal names
   review-pr-diff.sh as the path to use instead.
3. Fetches the PR's own full, paginated file list (one JSON string per file
   name, so a name holding a newline decodes intact) and its current
   headRefOid and baseRefOid directly from `gh`, together with the base branch
   name that read carries. Aborts on a baseRefOid that is not a full hex object
   name, on a base branch name that differs from step 2's, and on a listing
   whose length differs from the PR's own `changed_files` count.
4. Re-fetches headRefOid and the base branch name to catch a force-push or a
   retarget landing while the file list was being paginated.
5. Pipes the file list to audit-execution-surface.py. A stop verdict exits 3
   before any fetch of the PR's ref, naming the matched paths and reasons on
   stderr, one line per match with the path as an ASCII-only JSON string,
   bounded (review_pr_audit_match_report in _review-pr-lib.sh). An audit that
   fails to return a verdict exits 2 with its stderr shown
   (review_pr_audit_verdict in _review-pr-lib.sh defines what counts as a
   verdict).
6. On a clean audit, fetches refs/pull/<N>/head and asserts the fetched SHA
   equals the headRefOid fetched earlier in the same run. A mismatch means a
   force-push landed between audit and checkout, and aborts with no worktree
   left behind.
7. Only when the PR's base branch is not the base repository's default branch,
   audits every path the verified head changes from the default branch, since
   the checkout writes the whole head tree and a stacked base can carry files
   the PR's own file list never names. The default branch is fetched into
   refs/review-pr/pr-<N>-default, and `git diff --name-only -z
   <default>...<head>` is run twice, once to count its names and once to
   decode them, each run capped. A default-branch name git rejects as a ref
   name exits 2 before any fetch of it. A failed or capped run, a count that is
   not an integer, or a decoded list whose length differs from the count exits
   2 before the audit runs. The audit then runs as in step 5: a stop exits 3,
   naming review-pr-diff.sh, the base branch, and the matched paths on stderr,
   and a missing verdict exits 2.
8. Fetches the baseRefOid commit, since step 6 fetched only the PR's own ref,
   and writes the three-dot diff from baseRefOid to the verified headRefOid to
   $CONFIG_DIR/.review-pr-active.d/$SESSION_ID.diff, the path review-pr-diff.sh
   uses for its own diff. Aborts with no worktree created, and no diff file
   written, when the base fetch fails, the diff cannot be computed, or the diff
   is empty although the PR reports changed files. A failed diff write also
   aborts with no worktree created, but may leave an incomplete diff file.
9. Creates a new worktree under the main tree's .claude/worktrees/, named for
   this session and the PR number plus a random suffix. A second run against
   the same PR gets its own worktree; review-pr-finish.sh removes every
   worktree of the session.
10. Rewrites this session's provenance file with mode "checkout" (PR identity,
    the verified headRefOid, this session's Claude PID, the mode), never
    trusting review-pr-acquire.sh's mode "acquired" write.

Prints three or four lines on stdout on a successful run: the worktree's
absolute path, the diff file's absolute path, then the baseRefOid as a full hex
object name, then the fixed token BASE_IS_DEFAULT_BRANCH only when the PR's base
branch is the base repository's default branch.
Only an exit-0 run prints the diff path on stdout, and the caller reads the diff
from that output alone. review-pr-finish.sh removes the diff file a failed run
leaves behind.

Worst-case wall time: the per-step caps sum to 255 seconds on the success path
(10 for the two local git reads, 60 for the four gh calls, 55 for eleven jq
calls, 30 for the PR-ref fetch, 10 for two more local git reads, 30 for the
base fetch, 30 for the diff, 30 for worktree add) and 315 on the worst failure
path (a failed worktree add followed by its two capped cleanups). A PR whose
base branch is not the default branch adds 120 seconds to each path, for 375 and
435 (5 for the ref-name check, 30 for the default-branch fetch, 5 for resolving
it, 30 for the counting git run, 30 for the name-listing git run, 20 for four jq
calls). The name-listing run writes to a temporary file that the decoding jq
reads only after git has finished, so those two caps add rather than overlap.
Each cap kill adds up to 2 seconds of SIGKILL grace. The diff write and
the provenance write are uncapped, as is the audit script. The session lookup's
capped ps calls add up to 10 seconds per process-ancestor hop. The caps apply
only when `timeout` or `gtimeout` is on PATH; otherwise every step is uncapped.

Exit status: 0 on success, 3 when checkout is positively refused and
review-pr-diff.sh is the path to use instead (the PR's trust class, a
cross-repository head, or an audit stop verdict on the PR's files or on a
stacked base's delta), 2 on every other refusal or
failure the script itself detects, including a check that could not be completed.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

PR_IDENTITY="$1"

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"
# shellcheck source=_review-pr-lib.sh
. "$(dirname "$0")/_review-pr-lib.sh"

# Git trace output would reach the diff file and the failure messages that relay git's output.
# Config-sourced trace2.* targets are not unset here.
while IFS= read -r TRACE_VARIABLE_NAME; do
  case "$TRACE_VARIABLE_NAME" in
    GIT_TRACE* | GIT_CURL_VERBOSE*) unset "$TRACE_VARIABLE_NAME" ;;
  esac
done < <(compgen -e)
# GIT_DIFF_OPTS=-u<N> overrides --unified, so it would change the diff's context size.
unset GIT_DIFF_OPTS

# See _lib_parse_pr_identity (_lib.sh) for the split and validation.
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

# OWNER_REPO is compared against origin because headRefOid is content-addressed:
# a decoy repo can report the real PR's head commit while the ref fetch below
# always targets this worktree's origin, so the headRefOid equality check
# further down cannot catch the substitution.
ORIGIN_OWNER_REPO=$(_lib_origin_owner_repo "$REPO_ROOT") || {
  echo "review-pr-checkout.sh: could not resolve this worktree's origin remote, or parse an owner/repo out of its URL. Abort before any fetch." >&2
  exit 2
}
if _lib_case_insensitive_ne "$ORIGIN_OWNER_REPO" "$OWNER_REPO"; then
  echo "review-pr-checkout.sh: PR identity '$PR_IDENTITY' names repo '$OWNER_REPO', which does not match this worktree's own origin remote ('$ORIGIN_OWNER_REPO'). Abort before any fetch -- see the comment above this check in review-pr-checkout.sh for the cross-repo substitution it exists to close." >&2
  exit 2
fi

# claude-skills/skills/review-pr/ stows to $CONFIG_DIR/skills/review-pr/
# (stow-packages.sh), and this script runs the audit by that installed path.
# Anchoring on $CONFIG_DIR rather than a relative ../../.. guess
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
# Placed before the headRefOid fetch and the paginated file-list call, so a
# refused PR never has its file list paginated.
# authorAssociation is not a `gh pr view --json` field
# (claude-skills/skills/review-pr/REFERENCES.md), so this REST call is the
# only way to get it; it also carries head.repo/base.repo, deriving cross-repo
# status independently of step 1's own isCrossRepository field rather than
# trusting that upstream read.
# Trust classification widens the stop conditions below; it never removes
# one -- a MEMBER/OWNER author paired with a cross-repository PR still
# refuses via the cross-repo check further down, regardless of standing.
GH_PR_TRUST_TIMEOUT_SECONDS=10
if ! TRUST_JSON=$(_lib_gh "$GH_PR_TRUST_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER" 2>/dev/null); then
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
# A jq failure is not that verdict, so it exits 2 rather than reading as empty.
if ! HEAD_REPO_FULL_NAME=$(printf '%s' "$TRUST_JSON" | _lib_jq -r '.head.repo.full_name // empty' 2>/dev/null) \
  || ! BASE_REPO_FULL_NAME=$(printf '%s' "$TRUST_JSON" | _lib_jq -r '.base.repo.full_name // empty' 2>/dev/null); then
  echo "review-pr-checkout.sh: could not read PR $OWNER_REPO#$PR_NUMBER's head and base repositories from its trust-classification data (jq failed or timed out), so whether it is cross-repository is undecided. Abort before any fetch." >&2
  exit 2
fi
# Any spelling not listed here refuses, including FIRST_TIMER, MANNEQUIN, and
# a value GitHub adds later.
case "$AUTHOR_ASSOCIATION" in
  MEMBER | OWNER | COLLABORATOR | CONTRIBUTOR) ;;
  *)
    echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's author association is $AUTHOR_ASSOCIATION, outside the MEMBER/OWNER/COLLABORATOR/CONTRIBUTOR set -- checkout is refused unconditionally for this trust class. Use ~/.claude/scripts/review-pr-diff.sh instead. Abort before any fetch." >&2
    exit "$EXIT_CHECKOUT_REFUSED"
    ;;
esac
if [[ -z "$HEAD_REPO_FULL_NAME" || "$HEAD_REPO_FULL_NAME" != "$BASE_REPO_FULL_NAME" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER is cross-repository (head repo '$HEAD_REPO_FULL_NAME' vs base repo '$BASE_REPO_FULL_NAME') -- checkout is refused unconditionally for this trust class, regardless of author standing. Use ~/.claude/scripts/review-pr-diff.sh instead. Abort before any fetch." >&2
  exit "$EXIT_CHECKOUT_REFUSED"
fi
# The checkout writes the whole head tree, so for a base other than the default branch every path the head changes from the default branch is audited too.
if ! BASE_REF_NAME=$(printf '%s' "$TRUST_JSON" | _lib_jq -r '.base.ref // empty' 2>/dev/null) \
  || ! BASE_DEFAULT_BRANCH=$(printf '%s' "$TRUST_JSON" | _lib_jq -r '.base.repo.default_branch // empty' 2>/dev/null) \
  || [[ -z "$BASE_REF_NAME" || -z "$BASE_DEFAULT_BRANCH" ]]; then
  echo "review-pr-checkout.sh: could not read PR $OWNER_REPO#$PR_NUMBER's base branch and the base repository's default branch from its trust-classification data (jq failed or timed out, or a field was absent), so whether it targets the default branch is undecided. Abort before any fetch." >&2
  exit 2
fi

# REST snake_case, not the camelCase `changedFiles` of `gh pr view --json`:
# TRUST_JSON comes from `gh api`. The count is the ground truth the file
# listing below is checked against.
if ! CHANGED_FILES_COUNT=$(review_pr_rest_changed_files "$TRUST_JSON"); then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's REST payload carried no usable changed_files count (malformed response). Abort before any fetch -- the file listing below could not be checked for completeness." >&2
  exit 2
fi

# 10s: a network GET carrying no payload, the same budget review-pr-post.sh
# uses for its own gh pr view identity re-fetch.
GH_PR_VIEW_TIMEOUT_SECONDS=10
PR_VIEW_JSON=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid,baseRefOid,baseRefName 2>/dev/null) || PR_VIEW_JSON=""
if ! HEAD_REF_OID=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.headRefOid // empty' 2>/dev/null) \
  || [[ -z "$HEAD_REF_OID" ]]; then
  echo "review-pr-checkout.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's current headRefOid. Abort before any fetch of the PR's ref." >&2
  exit 2
fi
# baseRefOid goes into a `git fetch` and a `git diff` argument below, so only a
# full SHA-1 or SHA-256 hex object name passes: anything else could read as an
# option or a ref name.
if ! BASE_REF_OID=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.baseRefOid // empty' 2>/dev/null) \
  || ! review_pr_is_full_hex_oid "$BASE_REF_OID"; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's baseRefOid is missing or is not a full hex object name. Abort before any fetch of the PR's ref." >&2
  exit 2
fi
# The stacked-or-default decision below uses BASE_REF_NAME from the REST payload, while baseRefOid comes from this read.
# A retarget between the two reads would pair a base commit with the other base branch's decision, so the names must agree.
if ! PR_VIEW_BASE_REF_NAME=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.baseRefName // empty' 2>/dev/null) \
  || [[ "$PR_VIEW_BASE_REF_NAME" != "$BASE_REF_NAME" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's base branch reads as $(review_pr_ascii_json_string "$BASE_REF_NAME") from the REST payload but as $(review_pr_ascii_json_string "$PR_VIEW_BASE_REF_NAME") alongside baseRefOid (or the second read failed) -- a retarget between the two reads, so whether the base is the default branch is undecided. Abort before any fetch of the PR's ref." >&2
  exit 2
fi

# 30s, not the 10s above: this walks every page of the files listing via
# --paginate, which can be several round trips for a large PR, unlike the
# single-GET headRefOid call above.
GH_PR_FILES_TIMEOUT_SECONDS=30
# The REST files endpoint with --paginate, not `gh pr view --json files`,
# which silently caps at 100 entries with no --paginate equivalent
# (claude-skills/skills/review-pr/REFERENCES.md) -- this is exactly the
# full, paginated list the audit below must see, self-fetched rather than
# trusted from Step 1's own read.
# per_page=100 is GitHub's maximum page size and the size gh --paginate requests by default.
FILES_FETCH_STATUS=0
RAW_FILES=$(_lib_gh "$GH_PR_FILES_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/files?per_page=100" --paginate --jq "$REVIEW_PR_FILE_NAMES_JQ_FILTER" 2>/dev/null) || FILES_FETCH_STATUS=$?
if [[ "$FILES_FETCH_STATUS" -ne 0 ]]; then
  echo "review-pr-checkout.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's file list (gh api --paginate $(review_pr_gh_status_description "$FILES_FETCH_STATUS")). Abort before any fetch of the PR's ref -- a partial or failed listing must never be audited as if it were the full, or an empty, file set." >&2
  exit 2
fi

# Decodes those JSON string literals into the JSON array
# audit-execution-surface.py's stdin contract requires. A separate step so a
# decode failure reports "could not decode", not "could not fetch".
if ! FILES_JSON=$(review_pr_decode_file_names "$RAW_FILES"); then
  echo "review-pr-checkout.sh: could not decode PR $OWNER_REPO#$PR_NUMBER's file list as JSON. Abort before any fetch of the PR's ref." >&2
  exit 2
fi

# The REST files listing stops short on a very large PR without any error, and
# a push between the count read and the listing changes the count. Either way a
# listing whose length differs from the PR's own count must not be audited as
# if it were the full set.
if ! LISTED_FILES_COUNT=$(review_pr_file_count_matches "$FILES_JSON" "$CHANGED_FILES_COUNT"); then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's file list has ${LISTED_FILES_COUNT:-an unreadable number of} entries but the PR reports $CHANGED_FILES_COUNT changed files -- the listing is truncated, or the PR changed while it was being fetched. Retry only helps in the second case; abort before any fetch of the PR's ref." >&2
  exit 2
fi

# Re-fetch headRefOid and the base branch after the file list to catch a force-push or a retarget during
# pagination, before the audit runs against a possibly-stale list. The two values print one per line.
HEAD_AND_BASE_RECHECK=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid,baseRefName --jq '.headRefOid, .baseRefName' 2>/dev/null) || HEAD_AND_BASE_RECHECK=""
HEAD_REF_OID_RECHECK=$(printf '%s\n' "$HEAD_AND_BASE_RECHECK" | sed -n '1p')
BASE_REF_NAME_RECHECK=$(printf '%s\n' "$HEAD_AND_BASE_RECHECK" | sed -n '2p')
if [[ -z "$HEAD_REF_OID_RECHECK" || -z "$BASE_REF_NAME_RECHECK" ]]; then
  echo "review-pr-checkout.sh: could not re-fetch PR $OWNER_REPO#$PR_NUMBER's headRefOid and base branch to confirm the file list above is still current. Abort before any fetch of the PR's ref." >&2
  exit 2
fi
if [[ "$HEAD_REF_OID_RECHECK" != "$HEAD_REF_OID" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's headRefOid changed from $HEAD_REF_OID to $HEAD_REF_OID_RECHECK while its file list was being fetched -- a force-push race between the two self-fetches. Abort before any fetch of the PR's ref." >&2
  exit 2
fi
if [[ "$BASE_REF_NAME_RECHECK" != "$BASE_REF_NAME" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's base branch changed from $(review_pr_ascii_json_string "$BASE_REF_NAME") to $(review_pr_ascii_json_string "$BASE_REF_NAME_RECHECK") while its file list was being fetched -- a retarget race between the self-fetches, so the file list and the stacked-base decision may describe different bases. Abort before any fetch of the PR's ref." >&2
  exit 2
fi

# audit_paths_or_exit PATHS_JSON STOP_TEXT NO_VERDICT_TEXT
# Runs the audit over PATHS_JSON, returning only for a clean verdict.
# A stop prints "review-pr-checkout.sh: STOP_TEXT Matched paths:" and the matches on stderr, then exits 3.
# Anything but a verdict prints NO_VERDICT_TEXT after the audit's exit status and first stdout line, then exits 2.
# See review_pr_audit_verdict (_review-pr-lib.sh) for how the status and stdout are read, and why the audit
# runs as `python3 -I` with its stderr unredirected. The status is captured
# through the if/else exemption from `set -e` (shell-script-conventions.md)
# rather than a bare pipeline, so the stop path can still print the audit's own
# named matches before exiting.
audit_paths_or_exit() {
  local paths_json="$1" stop_text="$2" no_verdict_text="$3" audit_output audit_exit matches
  if audit_output=$(printf '%s' "$paths_json" | python3 -I "$AUDIT_SCRIPT"); then
    audit_exit=0
  else
    audit_exit=$?
  fi

  case "$(review_pr_audit_verdict "$audit_exit" "$audit_output")" in
    clean) ;;
    stop)
      matches=$(review_pr_audit_match_report "$audit_output")
      echo "review-pr-checkout.sh: $stop_text Matched paths:" >&2
      printf '%s\n' "$matches" >&2
      exit "$EXIT_CHECKOUT_REFUSED"
      ;;
    *)
      echo "review-pr-checkout.sh: audit-execution-surface.py returned no verdict (exit $audit_exit, first line of stdout: $(review_pr_audit_stdout_excerpt "$audit_output")), $no_verdict_text" >&2
      exit 2
      ;;
  esac
}

audit_paths_or_exit "$FILES_JSON" \
  "passive-execution audit stopped PR $OWNER_REPO#$PR_NUMBER before checkout -- no refs/pull/$PR_NUMBER/head fetch was made. Use ~/.claude/scripts/review-pr-diff.sh instead." \
  "so the PR's file list was not audited -- a tooling failure, not a stop (any stderr the audit wrote is above). Abort before any fetch of the PR's ref."

# Local ref namespace scoped to this script, distinct from any branch name a
# contributor might already have locally, so this fetch can never collide
# with or overwrite an unrelated ref.
# --no-tags stops git's default fetch of tags that point into the fetched history.
# --no-write-fetch-head leaves FETCH_HEAD alone.
# An empty --refmap= makes git ignore the remote's configured fetch refspecs (git-fetch(1)), which would otherwise also update a matching remote-tracking ref.
# Together the fetch writes only this ref among this repository's refs, tags and FETCH_HEAD.
# GIT_TERMINAL_PROMPT=0 on each network fetch makes git fail instead of prompting on the terminal itself; prompts that ssh raises are not covered.
LOCAL_REF="refs/review-pr/pr-$PR_NUMBER"
if GIT_TERMINAL_PROMPT=0 _lib_capped_for "$REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS" git -C "$REPO_ROOT" -c core.hooksPath=/dev/null fetch origin "refs/pull/$PR_NUMBER/head:$LOCAL_REF" --force --no-tags --no-write-fetch-head --refmap= >/dev/null 2>&1; then
  FETCH_STATUS=0
else
  FETCH_STATUS=$?
fi
if [[ "$FETCH_STATUS" -ne 0 ]]; then
  if _lib_status_consistent_with_cap_kill "$FETCH_STATUS"; then
    echo "review-pr-checkout.sh: could not fetch refs/pull/$PR_NUMBER/head for PR $OWNER_REPO#$PR_NUMBER from origin: git fetch exited $FETCH_STATUS, consistent with the ${REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS}s cap firing. Abort." >&2
  else
    echo "review-pr-checkout.sh: could not fetch refs/pull/$PR_NUMBER/head for PR $OWNER_REPO#$PR_NUMBER from origin. Abort." >&2
  fi
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

# _lib_main_repo_root, not $REPO_ROOT: the worktree is created under the main
# tree regardless of which tree this script itself stands in, and
# review-pr-finish.sh discovers it from the same root. The delta listing, the
# base fetch and the diff also run there, because git diff reads .gitattributes
# from the tree it runs in and a prior review worktree holds PR-supplied
# attributes. Every other use of $REPO_ROOT in this script (fetch, rev-parse,
# remote get-url) is unaffected: those correctly hit the shared object/ref/config
# store from either tree.
MAIN_REPO_ROOT=$(_lib_main_repo_root) || {
  echo "review-pr-checkout.sh: could not resolve this repository's main tree root (not inside a git repository, or git older than 2.31, which lacks rev-parse --path-format). Abort with no worktree created." >&2
  exit 2
}

# A hang backstop for a stalled git or filesystem, not a latency budget,
# following _LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS. 30s is a chosen
# ceiling, not a measured figure, and larger than _lib_capped's 5s default
# because the diff's cost grows with the PR's size. Caps the delta listing
# below and the diff after it.
GIT_DIFF_TIMEOUT_SECONDS=30

# exit_delta_unaudited REASON
# Exits 2 for a failure that left the stacked base's delta unaudited, so it is never read as a clean audit.
# Reads BASE_DEFAULT_BRANCH_ESCAPED and BASE_REF_NAME_ESCAPED, which the stacked-base block below sets before any call.
exit_delta_unaudited() {
  echo "review-pr-checkout.sh: could not list the paths PR $OWNER_REPO#$PR_NUMBER's head changes from default branch $BASE_DEFAULT_BRANCH_ESCAPED, so base branch $BASE_REF_NAME_ESCAPED was not audited: $1. Abort with no worktree created." >&2
  exit 2
}

# Note appended to the decode and count-match error messages, naming a jq that truncates raw input at NUL as their cause.
JQ_NUL_NOTE="a jq that truncates raw input at NUL may cause this; claude-config's README.md jq version bullet names the verified version"

# delta_diff_status_description STATUS
# Describes a failed delta diff run's status, adding the usual cause for git's own failure status 128.
delta_diff_status_description() {
  review_pr_git_status_description "$1" "$GIT_DIFF_TIMEOUT_SECONDS"
  if [[ "$1" == "128" ]]; then
    printf ' (often no merge base between the default branch and the head)'
  fi
}

# The checkout writes the whole head tree, so a base other than the default
# branch can carry a file the audit above would stop on that the PR's own file
# list never names. The delta is the head's changes from the default branch.
# The delta runs head-side, after the PR-ref fetch: a base-side list would miss a
# file that was on the base when the PR forked and was later removed from it.
if ! review_pr_base_is_default_branch "$BASE_REF_NAME" "$BASE_DEFAULT_BRANCH"; then
  # Both names come from the API, so messages print them through review_pr_ascii_json_string.
  BASE_DEFAULT_BRANCH_ESCAPED=$(review_pr_ascii_json_string "$BASE_DEFAULT_BRANCH")
  BASE_REF_NAME_ESCAPED=$(review_pr_ascii_json_string "$BASE_REF_NAME")

  # check-ref-format on the full ref rather than `--branch`, because the fetch below names the full ref, so a
  # name starting with `-` cannot read as an option and one holding `:` cannot add a refspec destination.
  if ! _lib_capped git -C "$MAIN_REPO_ROOT" check-ref-format "refs/heads/$BASE_DEFAULT_BRANCH" >/dev/null 2>&1; then
    echo "review-pr-checkout.sh: the base repository's default branch name $BASE_DEFAULT_BRANCH_ESCAPED is not a valid git ref name (or the check did not complete), so base branch $BASE_REF_NAME_ESCAPED cannot be audited against it. Abort before any fetch of it." >&2
    exit 2
  fi

  # --no-tags, --no-write-fetch-head and --refmap= as for LOCAL_REF above.
  DEFAULT_LOCAL_REF="refs/review-pr/pr-$PR_NUMBER-default"
  if GIT_TERMINAL_PROMPT=0 _lib_capped_for "$REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" -c core.hooksPath=/dev/null fetch origin "refs/heads/$BASE_DEFAULT_BRANCH:$DEFAULT_LOCAL_REF" --force --no-tags --no-write-fetch-head --refmap= >/dev/null 2>&1; then
    DEFAULT_FETCH_STATUS=0
  else
    DEFAULT_FETCH_STATUS=$?
  fi
  if [[ "$DEFAULT_FETCH_STATUS" -ne 0 ]]; then
    exit_delta_unaudited "the fetch of default branch $BASE_DEFAULT_BRANCH_ESCAPED from origin $(review_pr_git_status_description "$DEFAULT_FETCH_STATUS" "$REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS")"
  fi
  DEFAULT_OID=$(_lib_capped git -C "$MAIN_REPO_ROOT" rev-parse --verify --quiet "$DEFAULT_LOCAL_REF^{commit}" 2>/dev/null) || DEFAULT_OID=""
  # The value reaches a git revision argument below, so only a full hex object name passes, as for BASE_REF_OID.
  if ! review_pr_is_full_hex_oid "$DEFAULT_OID"; then
    exit_delta_unaudited "the fetched ref $DEFAULT_LOCAL_REF did not resolve to a commit with a full hex object name"
  fi

  # -z prints each name verbatim and NUL-terminated, where it would quote an unusual one. --no-renames lists both sides of a rename.
  # The head-side list needs a merge base, so a head with none fails the run, which exits 2 below.
  DELTA_DIFF_ARGS=(diff --name-only -z --no-renames --no-ext-diff --no-textconv --ignore-submodules=none "$DEFAULT_OID...$FETCHED_SHA" --)

  # The audit reads an empty list as clean, so every status below fails closed. The counting pipeline runs under pipefail,
  # so git's own status survives it. The name-listing run redirects to a file, so its status is git's alone.
  # The counting run reads names as NUL bytes alone, which no decoder can truncate.
  # LC_ALL=C because BSD tr fails on invalid UTF-8 under a UTF-8 locale.
  if DELTA_COUNT_RAW=$(_lib_capped_for "$GIT_DIFF_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" "${DELTA_DIFF_ARGS[@]}" 2>/dev/null | LC_ALL=C tr -cd '\000' | LC_ALL=C wc -c); then
    DELTA_COUNT_STATUS=0
  else
    DELTA_COUNT_STATUS=$?
  fi
  if [[ "$DELTA_COUNT_STATUS" -ne 0 ]]; then
    exit_delta_unaudited "the counting run of git diff $(delta_diff_status_description "$DELTA_COUNT_STATUS")"
  fi
  if ! DELTA_NAME_COUNT=$(review_pr_bare_count "$DELTA_COUNT_RAW"); then
    exit_delta_unaudited "the counting run did not print an integer"
  fi

  # The name run writes to a file and the decode reads it afterward, so git's status stays apart from the decoder's
  # and the decoder's own cap starts only once git has finished.
  if ! DELTA_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/review-pr-checkout.XXXXXX"); then
    exit_delta_unaudited "could not create a temporary directory for the name-listing run"
  fi
  # `|| true` keeps a failed cleanup from replacing the exit status the script chose, because errexit applies inside the trap.
  trap 'rm -rf -- "${DELTA_TMP_DIR:?}" || true' EXIT
  DELTA_NAMES_FILE="$DELTA_TMP_DIR/names"
  if _lib_capped_for "$GIT_DIFF_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" "${DELTA_DIFF_ARGS[@]}" >"$DELTA_NAMES_FILE" 2>/dev/null; then
    DELTA_NAMES_STATUS=0
  else
    DELTA_NAMES_STATUS=$?
  fi
  if [[ "$DELTA_NAMES_STATUS" -ne 0 ]]; then
    exit_delta_unaudited "the name-listing run of git diff $(delta_diff_status_description "$DELTA_NAMES_STATUS")"
  fi
  if ! DELTA_FILES_JSON=$(review_pr_nul_names_to_json <"$DELTA_NAMES_FILE"); then
    exit_delta_unaudited "the decoding of the name-listing run's output failed or timed out ($JQ_NUL_NOTE)"
  fi
  if ! DELTA_LISTED_COUNT=$(review_pr_file_count_matches "$DELTA_FILES_JSON" "$DELTA_NAME_COUNT"); then
    exit_delta_unaudited "the decoded name list has ${DELTA_LISTED_COUNT:-an unreadable number of} entries but the counting run found $DELTA_NAME_COUNT names ($JQ_NUL_NOTE)"
  fi

  audit_paths_or_exit "$DELTA_FILES_JSON" \
    "passive-execution audit stopped PR $OWNER_REPO#$PR_NUMBER before checkout -- its head changes paths from default branch $BASE_DEFAULT_BRANCH_ESCAPED that its base branch $BASE_REF_NAME_ESCAPED carries, and the checkout writes the whole head tree. Use ~/.claude/scripts/review-pr-diff.sh instead." \
    "so the paths the PR's head changes from default branch $BASE_DEFAULT_BRANCH_ESCAPED were not audited -- a tooling failure, not a stop (any stderr the audit wrote is above). Abort with no worktree created."
fi

review_pr_resolve_session_and_pid "review-pr-checkout.sh" " -- cannot name the worktree or record provenance" "Abort before creating a worktree." || exit 2

# The diff goes to a file because the harness truncates a large Bash result, so
# a large PR would reach the review cut short. The truncation threshold is in
# `claude-skills/skills/subagent-delegation/REFERENCES.md` § "Heavy command output — harness truncation and check-suite sizes".
# The path is the fixed per-session path review-pr-diff.sh also writes, so
# review-pr-finish.sh removes it.
# The base fetch is needed because the PR-ref fetch above brought only the PR's
# own commits, so the base commit may be absent locally.
# The diff compares two fetched commits, so it runs before the worktree exists
# and a failure leaves none behind.
# --no-write-fetch-head keeps it to object transfer, and `--` ends option parsing before the remote and object name.
if BASE_FETCH_OUTPUT=$(GIT_TERMINAL_PROMPT=0 _lib_capped_for "$REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" -c core.hooksPath=/dev/null fetch --no-write-fetch-head -- origin "$BASE_REF_OID" 2>&1); then
  BASE_FETCH_STATUS=0
else
  BASE_FETCH_STATUS=$?
fi
if [[ "$BASE_FETCH_STATUS" -ne 0 ]]; then
  if _lib_status_consistent_with_cap_kill "$BASE_FETCH_STATUS"; then
    echo "review-pr-checkout.sh: could not fetch baseRefOid $BASE_REF_OID for PR $OWNER_REPO#$PR_NUMBER from origin: git fetch exited $BASE_FETCH_STATUS, consistent with the ${REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS}s cap firing: $BASE_FETCH_OUTPUT. Abort with no worktree created." >&2
  else
    echo "review-pr-checkout.sh: could not fetch baseRefOid $BASE_REF_OID for PR $OWNER_REPO#$PR_NUMBER from origin: $BASE_FETCH_OUTPUT. Abort with no worktree created." >&2
  fi
  exit 2
fi
# --no-ext-diff and --no-textconv keep a configured diff driver from running.
# claude-skills/skills/review-pr/REFERENCES.md's "The checkout-mode diff follows
# some local git config" bullet lists which other config the remaining flags pin
# and which still applies.
GIT_DIFF_ARGS=(diff --no-ext-diff --no-textconv --no-color --ignore-submodules=none --src-prefix=a/ --dst-prefix=b/ --unified=3)
if DIFF_TEXT=$(_lib_capped_for "$GIT_DIFF_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" "${GIT_DIFF_ARGS[@]}" "$BASE_REF_OID...$FETCHED_SHA" -- 2>/dev/null); then
  DIFF_STATUS=0
else
  DIFF_STATUS=$?
fi
if [[ "$DIFF_STATUS" -ne 0 ]]; then
  if _lib_status_consistent_with_cap_kill "$DIFF_STATUS"; then
    # A cap kill exits 2, which review-pr's SKILL.md Step 2 reports and stops on,
    # leaving the human to re-run or ask for the diff-only path.
    echo "review-pr-checkout.sh: git diff $BASE_REF_OID...$FETCHED_SHA exited $DIFF_STATUS, consistent with the ${GIT_DIFF_TIMEOUT_SECONDS}s cap firing. No worktree was created and no diff file was written. This path does not switch to the diff-only path itself: report this to the user and stop, and the user may re-run the script or ask for the diff-only path." >&2
  else
    # The first run discards stderr so a warning cannot mix into the diff
    # text. A rerun with stdout discarded recovers git's own diagnostic, and
    # is skipped for a cap kill because it would wait out the cap again.
    DIFF_FAILURE_DETAIL=$(_lib_capped_for "$GIT_DIFF_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" "${GIT_DIFF_ARGS[@]}" "$BASE_REF_OID...$FETCHED_SHA" -- 2>&1 >/dev/null) || true
    echo "review-pr-checkout.sh: could not compute the diff $BASE_REF_OID...$FETCHED_SHA for PR $OWNER_REPO#$PR_NUMBER: ${DIFF_FAILURE_DETAIL:-git printed no diagnostic}. Abort with no worktree created." >&2
  fi
  exit 2
fi
# A three-dot diff is empty when the head is the base or already contained in
# it, so an empty diff for a PR that reports changed files means the base here
# is not the one the PR's file list was computed against.
if [[ -z "$DIFF_TEXT" && "$CHANGED_FILES_COUNT" -gt 0 ]]; then
  echo "review-pr-checkout.sh: git diff $BASE_REF_OID...$FETCHED_SHA is empty but PR $OWNER_REPO#$PR_NUMBER reports $CHANGED_FILES_COUNT changed files -- the head may already be contained in the base. Abort with no worktree created and no diff file written." >&2
  exit 2
fi
ACTIVE_DIR="$CONFIG_DIR/.review-pr-active.d"
if ! mkdir -p -- "$ACTIVE_DIR"; then
  echo "review-pr-checkout.sh: could not create the active directory $ACTIVE_DIR -- cannot record this diff. Abort with no worktree created." >&2
  exit 2
fi
DIFF_FILE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" diff)
if ! printf '%s\n' "$DIFF_TEXT" > "$DIFF_FILE"; then
  echo "review-pr-checkout.sh: could not write diff file $DIFF_FILE (the file may be incomplete). Abort with no worktree created." >&2
  exit 2
fi

# Every invocation gets its own mktemp directory, so no two runs share a path.
# review-pr-finish.sh finds the directory again by
# the session-scoped name (_lib_review_pr_worktree_template).
if ! mkdir -p -- "$MAIN_REPO_ROOT/.claude/worktrees" \
  || ! WORKTREE_DIR=$(mktemp -d "$(_lib_review_pr_worktree_template "$MAIN_REPO_ROOT" "$SESSION_ID" "$PR_NUMBER")"); then
  echo "review-pr-checkout.sh: could not create a directory for the review worktree under $MAIN_REPO_ROOT/.claude/worktrees. Abort with no worktree created." >&2
  exit 2
fi

# core.hooksPath=/dev/null disables hooks: a relative hooksPath resolves inside the PR's own tree, so post-checkout would run PR-supplied code.
if WORKTREE_ADD_OUTPUT=$(_lib_capped_for "$_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" -c core.hooksPath=/dev/null worktree add --detach "$WORKTREE_DIR" "$FETCHED_SHA" 2>&1); then
  WORKTREE_ADD_STATUS=0
else
  WORKTREE_ADD_STATUS=$?
fi
if [[ "$WORKTREE_ADD_STATUS" -ne 0 ]]; then
  # The diagnostic prints before any cleanup that can itself fail, so it
  # states what cleanup is attempted rather than what it achieved.
  ADD_FAILURE_CLEANUP_NOTE="Removing that directory and pruning its registration next; if a registered leftover remains, ~/.claude/scripts/review-pr-finish.sh finds it and retries its removal."
  if _lib_status_consistent_with_cap_kill "$WORKTREE_ADD_STATUS"; then
    echo "review-pr-checkout.sh: git worktree add for $WORKTREE_DIR exited $WORKTREE_ADD_STATUS, consistent with the ${_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS}s local git cap firing (a stalled git or filesystem, or a tree too large for the cap): $WORKTREE_ADD_OUTPUT. Abort. $ADD_FAILURE_CLEANUP_NOTE" >&2
  else
    echo "review-pr-checkout.sh: git worktree add failed for $WORKTREE_DIR: $WORKTREE_ADD_OUTPUT. Abort. $ADD_FAILURE_CLEANUP_NOTE" >&2
  fi
  # WORKTREE_DIR is the fresh mktemp path above, so nothing else owns it.
  # Prune skips a locked registration.
  _lib_capped_for "$_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS" rm -rf -- "${WORKTREE_DIR:?}" || true
  _lib_capped_for "$_LIB_REVIEW_PR_WORKTREE_OP_TIMEOUT_SECONDS" git -C "$MAIN_REPO_ROOT" worktree prune >/dev/null 2>&1 || true
  exit 2
fi

# Provenance write, mode "checkout" (see the usage text).
PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)
if ! _lib_write_review_pr_provenance "$PROVENANCE" \
  "pr_identity=$PR_IDENTITY" "head_ref_oid=$HEAD_REF_OID" "pid=$CLAUDE_PID" "mode=checkout"; then
  echo "review-pr-checkout.sh: could not write provenance file $PROVENANCE -- cannot record this checkout. The worktree at $WORKTREE_DIR was created; run ~/.claude/scripts/review-pr-finish.sh to clean it up. Abort." >&2
  exit 2
fi

printf '%s\n%s\n%s\n' "$WORKTREE_DIR" "$DIFF_FILE" "$BASE_REF_OID"
if review_pr_base_is_default_branch "$BASE_REF_NAME" "$BASE_DEFAULT_BRANCH"; then
  printf '%s\n' "$REVIEW_PR_BASE_IS_DEFAULT_BRANCH_TOKEN"
fi
