#!/usr/bin/env bash
# No-checkout review path for /review-pr's checkout-or-diff-only branch --
# review-pr-checkout.sh's trust block otherwise makes a cross-repo or
# first-time-contributor PR permanently unreviewable, since that class is
# exactly the common case on a public repo this skill exists to review.
# Mirrors review-pr-checkout.sh's self-derivation discipline (own
# PR-identity parse, own origin-identity check, own paginated file list,
# own double headRefOid fetch bracketing the pagination) minus everything
# that only matters once a PR is checked out: no worktree.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-diff.sh <owner>/<repo>#<number>

Self-derives the PR's own file list and headRefOid, the same discipline
review-pr-checkout.sh uses, but never checks the PR out. In order:
1. Checks that <owner>/<repo> matches this worktree's own origin remote,
   aborting before any gh call on a mismatch.
2. Fetches the PR's current headRefOid, baseRefOid, and baseRefName in one
   `gh pr view` call, and its REST `changed_files` count, base branch, and the
   base repository's default branch. Aborts when the base branch or the default
   branch is unreadable, or when the two base branch names differ.
3. Only when the PR's base branch is not the base repository's default branch,
   walks the chain of ancestor PRs behind it (review_pr_walk_stack in
   _review-pr-lib.sh), one `gh pr list` lookup per level, down to the default
   branch or to a branch with no open PR. Exits 4, before any file listing,
   when an ancestor PR has no submitted review (a review in any state counts, a
   pending one does not), naming the lowest such PR. A walk that cannot decide
   exits 2.
4. Stops naming the limit when the PR changes more than 300 files, a limit
   third parties report for GitHub's diff endpoint (see
   claude-skills/skills/review-pr/REFERENCES.md).
5. Fetches the full, paginated file list (one JSON string per file name, so a
   name holding a newline stays one name) and aborts when its length differs
   from `changed_files`.
6. Re-fetches headRefOid to catch a force-push landing while the file list was
   being paginated, and aborts on drift.
7. Pipes the file list to audit-execution-surface.py. A stop verdict is
   reported on stderr as a mandatory pre-seeded finding rather than a stop:
   with no checkout, that predicate has nothing to protect, and a PR
   touching .claude/hooks/** or .mcp.json is precisely what an inbound reviewer
   must flag. Each match is one stderr line with the path as an ASCII-only
   JSON string, bounded (review_pr_audit_match_report in _review-pr-lib.sh).
   An audit that fails to return a verdict aborts with exit 2 and its stderr
   shown, and is never reported as a finding (review_pr_audit_verdict in
   _review-pr-lib.sh defines what counts as a verdict).
8. Fetches the baseRefOid commit by object name, with git hooks disabled, so
   the session can read base files with `git show` and `git grep`. Only a full
   SHA-1 or SHA-256 hex object name is fetched. A baseRefOid that is missing,
   not hex, or cannot be fetched degrades the base only: the run continues and
   writes `review-pr-diff.sh: BASE_UNAVAILABLE <cause>` on stderr, where cause
   is missing, not-hex, fetch-failed (git exited non-zero, or exited 0 without
   leaving the commit in this repository), or fetch-timed-out (git exited with a
   status a cap kill produces). Git's own output is never echoed. A missing
   headRefOid still aborts.
9. Fetches `gh pr diff`. Aborts, writing no diff file and no provenance, when
   the diff is empty or whitespace-only although the PR reports changed
   files. Re-fetches
   headRefOid once more and aborts on drift, so the diff is bound to the head
   the provenance records.
10. Writes the diff to $CONFIG_DIR/.review-pr-active.d/$SESSION_ID.diff and
    rewrites this session's provenance file with mode "diff-only".

Prints the diff file's path on stdout as line 1 of a successful run. When the
base fetch succeeded it also prints the baseRefOid as a full hex object name on
line 2, and when the PR's base branch is also the base repository's default
branch, the fixed token BASE_IS_DEFAULT_BRANCH on line 3. When the base fetch did
not succeed, stdout holds the path alone, and the token is never printed.

Worst-case wall time: the per-step caps sum to 185 seconds on a run whose audit
is clean (15 for the three local git reads, 100 for the six gh calls, 40 for
eight jq calls, 30 for the base fetch) and 200 when the audit reports a finding
(three more jq calls). A PR whose base branch is not the default branch adds 120
seconds for the stack walk (eight lookups, each a 10 second gh call and a 5
second jq call), for 305 and 320.
Each cap kill adds up to 2 seconds of SIGKILL grace. The diff write and the
provenance write are uncapped, as is the audit script. The session lookup's
capped ps calls add up to 10 seconds per process-ancestor hop. The caps apply
only when `timeout` or `gtimeout` is on PATH; otherwise every step is uncapped.

Exit status: 0 on success, 4 when the PR's base branch is not the default branch
and an ancestor PR in its stack has no submitted review (step 3's gate;
review-pr-checkout.sh refuses the PR too), 2 on every other refusal or failure,
including a stack walk that could not decide.
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

GH_PR_VIEW_TIMEOUT_SECONDS=10

# confirm_head_ref_oid_unchanged CURRENT_SUBJECT FETCH_SUBJECT
# Exits 2 unless the PR's headRefOid still equals HEAD_REF_OID, the value the
# provenance records. CURRENT_SUBJECT names what the re-fetch confirms is still
# current, and FETCH_SUBJECT names what was being fetched during the window.
confirm_head_ref_oid_unchanged() {
  local head_ref_oid_recheck
  head_ref_oid_recheck=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || head_ref_oid_recheck=""
  if [[ -z "$head_ref_oid_recheck" ]]; then
    echo "review-pr-diff.sh: could not re-fetch PR $OWNER_REPO#$PR_NUMBER's headRefOid to confirm $1 is still current. Abort." >&2
    exit 2
  fi
  if [[ "$head_ref_oid_recheck" != "$HEAD_REF_OID" ]]; then
    echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER's headRefOid changed from $HEAD_REF_OID to $head_ref_oid_recheck while $2 was being fetched -- a force-push race. Abort." >&2
    exit 2
  fi
}

if ! PR_IDENTITY_FIELDS=$(_lib_parse_pr_identity "$PR_IDENTITY"); then
  echo "review-pr-diff.sh: PR identity '$PR_IDENTITY' is not a valid <owner>/<repo>#<number>." >&2
  usage
  exit 2
fi
OWNER_REPO=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '1p')
PR_NUMBER=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '2p')

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-diff.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty). Abort before any fetch." >&2
  exit 2
}

review_pr_resolve_session_and_pid "review-pr-diff.sh" "" "Abort before any fetch." || exit 2

REPO_ROOT=$(_lib_capped git rev-parse --show-toplevel 2>/dev/null) || REPO_ROOT=""
if [[ -z "$REPO_ROOT" ]]; then
  echo "review-pr-diff.sh: not inside a git repository. Abort before any fetch." >&2
  exit 2
fi

# Same cross-repo audit-substitution reasoning as review-pr-checkout.sh's
# own origin check (see the comment above that check in review-pr-checkout.sh):
# a PR's headRefOid is content-addressed, so an unchecked mismatch would let
# this script's audit and diff run against a decoy repo's manufactured content
# while claiming to describe the real PR.
ORIGIN_OWNER_REPO=$(_lib_origin_owner_repo "$REPO_ROOT") || {
  echo "review-pr-diff.sh: could not resolve this worktree's origin remote, or parse an owner/repo out of its URL. Abort before any fetch." >&2
  exit 2
}
if _lib_case_insensitive_ne "$ORIGIN_OWNER_REPO" "$OWNER_REPO"; then
  echo "review-pr-diff.sh: PR identity '$PR_IDENTITY' names repo '$OWNER_REPO', which does not match this worktree's own origin remote ('$ORIGIN_OWNER_REPO'). Abort before any fetch." >&2
  exit 2
fi

AUDIT_SCRIPT="$CONFIG_DIR/skills/review-pr/audit-execution-surface.py"
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "review-pr-diff.sh: audit script not found at $AUDIT_SCRIPT -- the review-pr skill is not installed under this config dir. Abort before any fetch." >&2
  exit 2
fi

PR_VIEW_JSON=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid,baseRefOid,baseRefName 2>/dev/null) || PR_VIEW_JSON=""
HEAD_REF_OID=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.headRefOid // empty' 2>/dev/null) || HEAD_REF_OID=""
if [[ -z "$HEAD_REF_OID" ]]; then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's current headRefOid. Abort." >&2
  exit 2
fi
# A baseRefOid that cannot be read degrades the base only: the diff and the audit do not depend on it.
BASE_REF_OID=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.baseRefOid // empty' 2>/dev/null) || BASE_REF_OID=""
# The base branch name decides the stack gate and the default-branch token, so an unreadable one aborts below.
PR_VIEW_BASE_REF_NAME=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.baseRefName // empty' 2>/dev/null) || PR_VIEW_BASE_REF_NAME=""

# The PR's own file count, from the REST payload (snake_case, unlike the
# camelCase `changedFiles` of `gh pr view --json`). Ground truth for the
# precheck below and for the file listing's completeness.
# 10s: a network GET carrying no payload, the budget review-pr-acquire.sh uses
# for the same call.
GH_PR_REST_TIMEOUT_SECONDS=10
if ! PR_REST_JSON=$(_lib_gh "$GH_PR_REST_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER" 2>/dev/null); then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's changed_files count (gh api failed or timed out). Abort." >&2
  exit 2
fi
if ! CHANGED_FILES_COUNT=$(review_pr_rest_changed_files "$PR_REST_JSON"); then
  echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER's REST payload carried no usable changed_files count (malformed response). Abort." >&2
  exit 2
fi
REST_BASE_REF_NAME=$(printf '%s' "$PR_REST_JSON" | _lib_jq -r '.base.ref // empty' 2>/dev/null) || REST_BASE_REF_NAME=""
BASE_DEFAULT_BRANCH=$(printf '%s' "$PR_REST_JSON" | _lib_jq -r '.base.repo.default_branch // empty' 2>/dev/null) || BASE_DEFAULT_BRANCH=""
if [[ -z "$PR_VIEW_BASE_REF_NAME" || -z "$REST_BASE_REF_NAME" || -z "$BASE_DEFAULT_BRANCH" ]]; then
  echo "review-pr-diff.sh: could not read PR $OWNER_REPO#$PR_NUMBER's base branch from both gh pr view and the REST payload, and the base repository's default branch from the REST payload (jq failed or timed out, or a field was absent), so whether the PR is stacked is undecided. Abort." >&2
  exit 2
fi
# The stacked-or-default decision uses the REST payload's base name, while baseRefOid comes from gh pr view.
# A retarget between the two reads would pair a base commit with the other base branch's decision, so the names must agree.
if [[ "$PR_VIEW_BASE_REF_NAME" != "$REST_BASE_REF_NAME" ]]; then
  echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER's base branch reads as $(review_pr_ascii_json_string "$REST_BASE_REF_NAME" "$REVIEW_PR_STACK_TEXT_ECHO_LIMIT") from the REST payload but as $(review_pr_ascii_json_string "$PR_VIEW_BASE_REF_NAME" "$REVIEW_PR_STACK_TEXT_ECHO_LIMIT") from gh pr view -- a retarget between the two reads, so whether the PR is stacked is undecided. Abort." >&2
  exit 2
fi
BASE_REF_NAME="$REST_BASE_REF_NAME"

# A PR stacked on an ancestor with no submitted review is refused before the file listing, the diff fetch or any provenance write.
# A walk failure exits 2 here after the walk prints its own prefixed stderr line, so no gh status becomes this script's exit status (gh exits 4 when a command needs authentication). Only the gate below exits 4.
if ! review_pr_base_is_default_branch "$BASE_REF_NAME" "$BASE_DEFAULT_BRANCH"; then
  if ! LOWEST_UNREVIEWED_ANCESTOR=$(review_pr_walk_stack "review-pr-diff.sh" "$OWNER_REPO" "$PR_NUMBER" "$BASE_REF_NAME" "$BASE_DEFAULT_BRANCH" lowest-unreviewed); then
    exit 2
  fi
  if [[ "$LOWEST_UNREVIEWED_ANCESTOR" =~ ^[1-9][0-9]*$ ]]; then
    echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER is stacked on PR $OWNER_REPO#$LOWEST_UNREVIEWED_ANCESTOR, which has no submitted review. PR $OWNER_REPO#$LOWEST_UNREVIEWED_ANCESTOR clears once a review in any state is submitted on it on GitHub; a review kept local does not count. review-pr-checkout.sh refuses PR $OWNER_REPO#$PR_NUMBER too. Abort before any file listing." >&2
    exit "$REVIEW_PR_EXIT_STACK_UNREVIEWED"
  fi
  if [[ "$LOWEST_UNREVIEWED_ANCESTOR" != "clear" ]]; then
    echo "review-pr-diff.sh: the stack walk for PR $OWNER_REPO#$PR_NUMBER printed neither clear nor an ancestor PR number, so whether its ancestors have reviews is undecided. Abort before any file listing." >&2
    exit 2
  fi
fi

# The changed-file ceiling of GitHub's diff endpoint: `gh pr diff` cannot
# serve a PR past it, so a named stop here beats a bare gh failure below.
# [unverified] 300 is the limit in GitHub's 406 error text as third parties report it; no REST reference page states it.
DIFF_MAX_CHANGED_FILES=300
if [[ "$CHANGED_FILES_COUNT" -gt "$DIFF_MAX_CHANGED_FILES" ]]; then
  echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER changes $CHANGED_FILES_COUNT files, over the $DIFF_MAX_CHANGED_FILES-file limit third parties report for GitHub's diff endpoint -- no diff can be fetched. Abort; this PR needs a route other than gh pr diff." >&2
  exit 2
fi

GH_PR_FILES_TIMEOUT_SECONDS=30
# per_page=100 is GitHub's maximum page size and the size gh --paginate requests by default.
FILES_FETCH_STATUS=0
RAW_FILES=$(_lib_gh "$GH_PR_FILES_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/files?per_page=100" --paginate --jq "$REVIEW_PR_FILE_NAMES_JQ_FILTER" 2>/dev/null) || FILES_FETCH_STATUS=$?
if [[ "$FILES_FETCH_STATUS" -ne 0 ]]; then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's file list (gh api --paginate $(review_pr_gh_status_description "$FILES_FETCH_STATUS")). Abort -- a partial or failed listing must never be audited as if it were the full, or an empty, file set." >&2
  exit 2
fi

if ! FILES_JSON=$(review_pr_decode_file_names "$RAW_FILES"); then
  echo "review-pr-diff.sh: could not decode PR $OWNER_REPO#$PR_NUMBER's file list as JSON. Abort." >&2
  exit 2
fi

# A listing whose length differs from the PR's own count is truncated, or the
# PR changed between the count read and the listing; either way it must not be
# audited as if it were the full file set. No name is refused for its content
# here: nothing splits names on a delimiter, and this path reports audit hits
# instead of stopping on them.
if ! LISTED_FILES_COUNT=$(review_pr_file_count_matches "$FILES_JSON" "$CHANGED_FILES_COUNT"); then
  echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER's file list has ${LISTED_FILES_COUNT:-an unreadable number of} entries but the PR reports $CHANGED_FILES_COUNT changed files -- the listing is truncated, or the PR changed while it was being fetched. Retry only helps in the second case; abort." >&2
  exit 2
fi

# Same TOCTOU guard as review-pr-checkout.sh: a force-push landing between
# the initial headRefOid fetch and the file-list fetch would leave the
# audit running against a file list gh already considers stale.
confirm_head_ref_oid_unchanged "the file list above" "its file list"

# Unlike review-pr-checkout.sh, a stop verdict here is reported, not fatal: no
# checkout means no subject for the audit's own protection, while a PR touching
# .claude/hooks/** or .mcp.json is exactly what an inbound reviewer must flag in
# the review itself. Same predicate, different disposition. Anything but a
# verdict aborts instead of reporting a finding. See review_pr_audit_verdict
# (_review-pr-lib.sh) for how the status and stdout are read, and why the audit
# runs as `python3 -I` with its stderr unredirected.
if AUDIT_OUTPUT=$(printf '%s' "$FILES_JSON" | python3 -I "$AUDIT_SCRIPT"); then
  AUDIT_EXIT=0
else
  AUDIT_EXIT=$?
fi
case "$(review_pr_audit_verdict "$AUDIT_EXIT" "$AUDIT_OUTPUT")" in
  clean) ;;
  stop)
    MATCHES=$(review_pr_audit_match_report "$AUDIT_OUTPUT")
    echo "review-pr-diff.sh: AUDIT_FINDING -- passive-execution audit matched paths in PR $OWNER_REPO#$PR_NUMBER's file list. Treat this as a mandatory blocking finding in the synthesized review, not a stop -- this path checks nothing out. Matched paths:" >&2
    printf '%s\n' "$MATCHES" >&2
    ;;
  *)
    echo "review-pr-diff.sh: audit-execution-surface.py returned no verdict (exit $AUDIT_EXIT, first line of stdout: $(review_pr_audit_stdout_excerpt "$AUDIT_OUTPUT")), so the PR's file list was not audited -- a tooling failure, not a finding (any stderr the audit wrote is above). Abort." >&2
    exit 2
    ;;
esac

# No worktree exists on this path, so the base commit is fetched only for the
# session's own `git show` and `git grep` reads. A failure here never stops the
# run, and git's output is dropped because it can carry remote URLs into a
# posted body.
BASE_UNAVAILABLE_CAUSE=""
if [[ -z "$BASE_REF_OID" ]]; then
  BASE_UNAVAILABLE_CAUSE="missing"
elif ! review_pr_is_full_hex_oid "$BASE_REF_OID"; then
  # Only a full hex object name reaches `git fetch`: anything else could read as an option or a ref name.
  BASE_UNAVAILABLE_CAUSE="not-hex"
else
  # core.hooksPath=/dev/null disables hooks: a relative hooksPath resolves inside the repo's own tree.
  # --no-write-fetch-head keeps the fetch to object transfer, leaving the repo's FETCH_HEAD alone.
  # --recurse-submodules=no keeps git from also fetching into a populated submodule, whose failure would fail this fetch.
  # `--` ends option parsing before the remote and object name, independent of the hex check above.
  # GIT_TERMINAL_PROMPT=0 makes git fail instead of prompting on the terminal itself; prompts that ssh raises are not covered.
  BASE_FETCH_STATUS=0
  GIT_TERMINAL_PROMPT=0 _lib_capped_for "$REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS" git -C "$REPO_ROOT" -c core.hooksPath=/dev/null fetch --no-write-fetch-head --recurse-submodules=no -- origin "$BASE_REF_OID" >/dev/null 2>&1 || BASE_FETCH_STATUS=$?
  if _lib_status_consistent_with_cap_kill "$BASE_FETCH_STATUS"; then
    BASE_UNAVAILABLE_CAUSE="fetch-timed-out"
  elif [[ "$BASE_FETCH_STATUS" -ne 0 ]]; then
    BASE_UNAVAILABLE_CAUSE="fetch-failed"
  elif ! _lib_capped git -C "$REPO_ROOT" cat-file -e "$BASE_REF_OID^{commit}" >/dev/null 2>&1; then
    # A fetch can exit 0 without leaving the commit local, and the session's `git show` would then fail with raw git output.
    BASE_UNAVAILABLE_CAUSE="fetch-failed"
  fi
fi
if [[ -n "$BASE_UNAVAILABLE_CAUSE" ]]; then
  echo "review-pr-diff.sh: BASE_UNAVAILABLE $BASE_UNAVAILABLE_CAUSE" >&2
fi

GH_PR_DIFF_TIMEOUT_SECONDS=30
if ! DIFF_TEXT=$(_lib_gh "$GH_PR_DIFF_TIMEOUT_SECONDS" pr diff "$PR_NUMBER" -R "$OWNER_REPO" 2>/dev/null); then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's diff (gh pr diff failed or timed out). Abort." >&2
  exit 2
fi
# review-pr-checkout.sh also refuses an empty diff, but not a whitespace-only one.
# A diff that is empty or only whitespace for a PR that reports changed files is
# a transient or truncated server response, and reviewing it would read as "no
# findings".
if [[ ! "$DIFF_TEXT" =~ [^[:space:]] && "$CHANGED_FILES_COUNT" -gt 0 ]]; then
  echo "review-pr-diff.sh: gh pr diff for PR $OWNER_REPO#$PR_NUMBER printed no diff although the PR reports $CHANGED_FILES_COUNT changed files. Abort with no diff file written." >&2
  exit 2
fi
# A force-push landing during the diff fetch would leave the provenance naming
# a head the diff text does not describe.
confirm_head_ref_oid_unchanged "the diff above" "its diff"

ACTIVE_DIR="$CONFIG_DIR/.review-pr-active.d"
if ! mkdir -p -- "$ACTIVE_DIR"; then
  echo "review-pr-diff.sh: could not create the active directory $ACTIVE_DIR -- cannot record this diff. Abort." >&2
  exit 2
fi
DIFF_FILE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" diff)
if ! printf '%s\n' "$DIFF_TEXT" > "$DIFF_FILE"; then
  echo "review-pr-diff.sh: could not write diff file $DIFF_FILE. Abort." >&2
  exit 2
fi

PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)
if ! _lib_write_review_pr_provenance "$PROVENANCE" \
  "pr_identity=$PR_IDENTITY" "head_ref_oid=$HEAD_REF_OID" "pid=$CLAUDE_PID" "mode=diff-only"; then
  echo "review-pr-diff.sh: could not write provenance file $PROVENANCE. Abort." >&2
  exit 2
fi

printf '%s\n' "$DIFF_FILE"
if [[ -z "$BASE_UNAVAILABLE_CAUSE" ]]; then
  printf '%s\n' "$BASE_REF_OID"
  if review_pr_base_is_default_branch "$BASE_REF_NAME" "$BASE_DEFAULT_BRANCH"; then
    printf '%s\n' "$REVIEW_PR_BASE_IS_DEFAULT_BRANCH_TOKEN"
  fi
fi
