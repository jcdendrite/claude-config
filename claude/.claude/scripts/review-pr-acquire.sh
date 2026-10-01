#!/usr/bin/env bash
# The acquire step for /review-pr: one script call running the `gh` calls,
# so the pagination reconciliation and the review-thread fetch are performed
# the same way on every run rather than left to the model's own transcription.
# Needs no active-bypass marker (see require-respond-pr.sh's header), so this
# script must never make a write-shaped `gh api`/`gh pr` call.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-acquire.sh <owner>/<repo>#<number>

Fetches everything /review-pr's later steps need in one call:
- gh pr view's own metadata fields, including statusCheckRollup (entries
  passed through raw, a null value read as an empty list).
- author_association and the PR's own `changed_files`/`commits` totals, from a
  separate REST call (author_association is not a valid `gh pr view --json`
  field).
- The full paginated changed-files and commits lists, re-fetched via
  --paginate when gh pr view's own capped fields disagree with the PR's real
  totals.
- Existing review bodies and existing inline review comments.

Prints one JSON document on stdout and writes the identical document to
$CONFIG_DIR/.review-pr-active.d/$SESSION_ID.context.json, a backstop against
a harness-truncated stdout on a large PR. The file is pretty-printed
(multi-line) so the Read tool can page it with offset/limit. Its path is
printed on stderr before the document is printed on stdout.

The document carries `filesComplete` and `commitsComplete` as its first two
keys, each true only when the list's length equals the PR's own REST total.
A re-fetched file list that still differs in length from `changed_files`
aborts instead.

Also writes this session's provenance file (mode "acquired").
review-pr-checkout.sh and review-pr-diff.sh each rewrite it (mode
"checkout"/"diff-only") after their own independent re-derivation.
marker.sh write review-pr accepts only those two modes, so an acquire-only
session can never write a completion marker.

Any gh failure aborts with no partial document written. gh's own
stderr/error text is never echoed verbatim, since an API error payload can
echo request parameters: only this script's own fixed messages reach
stdout/stderr.
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

if ! PR_IDENTITY_FIELDS=$(_lib_parse_pr_identity "$PR_IDENTITY"); then
  echo "review-pr-acquire.sh: PR identity '$PR_IDENTITY' is not a valid <owner>/<repo>#<number>." >&2
  usage
  exit 2
fi
OWNER_REPO=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '1p')
PR_NUMBER=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '2p')

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-acquire.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty). Abort before any fetch." >&2
  exit 2
}

# _lib_resolve_claude_pid prints "<session_id> <pid>" -- the provenance
# file's PID field is this session's own Claude Code process, the same
# value marker.sh activate stores for every other skill's bypass marker.
SESSION_AND_PID=$(_lib_resolve_claude_pid) || {
  echo "review-pr-acquire.sh: could not resolve this session's id (capture-session-id.sh SessionStart hook did not run). Abort before any fetch." >&2
  exit 2
}
SESSION_ID="${SESSION_AND_PID%% *}"
CLAUDE_PID="${SESSION_AND_PID##* }"
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  echo "review-pr-acquire.sh: resolved session id '$SESSION_ID' is not a valid path component. Abort before any fetch." >&2
  exit 2
fi

ACTIVE_DIR="$CONFIG_DIR/.review-pr-active.d"
if ! mkdir -p -- "$ACTIVE_DIR"; then
  echo "review-pr-acquire.sh: could not create the active directory $ACTIVE_DIR. Abort before any fetch." >&2
  exit 2
fi

# 10s: a network GET carrying no payload, matching review-pr-checkout.sh's
# own budget for the same call shape.
GH_PR_VIEW_TIMEOUT_SECONDS=10
PR_VIEW_FIELDS="title,body,author,isCrossRepository,baseRefOid,headRefOid,headRepositoryOwner,files,changedFiles,commits,reviewDecision,mergeable,mergeStateStatus,statusCheckRollup"
if ! PR_VIEW_JSON=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json "$PR_VIEW_FIELDS" 2>/dev/null); then
  echo "review-pr-acquire.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's metadata (gh pr view failed or timed out). Abort." >&2
  exit 2
fi
if ! printf '%s' "$PR_VIEW_JSON" | _lib_jq -e 'type == "object"' >/dev/null 2>&1; then
  echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's metadata response was not a JSON object. Abort." >&2
  exit 2
fi

HEAD_REF_OID=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.headRefOid // empty' 2>/dev/null) || HEAD_REF_OID=""
if [[ -z "$HEAD_REF_OID" ]]; then
  echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's metadata carried no headRefOid. Abort." >&2
  exit 2
fi

# authorAssociation is not a valid `gh pr view --json` field -- including it
# errors the whole call rather than degrading (REFERENCES.md). This single
# REST call also carries the PR's own true commit count (`.commits`, an
# integer -- distinct from gh pr view's own "commits" field, which is a
# capped array) and true file count (`.changed_files`), so it doubles as the
# reconciliation source for both lists below.
GH_PR_REST_TIMEOUT_SECONDS=10
if ! PR_REST_JSON=$(_lib_gh "$GH_PR_REST_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER" 2>/dev/null); then
  echo "review-pr-acquire.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's author_association (gh api failed or timed out). Abort." >&2
  exit 2
fi
if ! printf '%s' "$PR_REST_JSON" | _lib_jq -e 'type == "object"' >/dev/null 2>&1; then
  echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's author_association response was not a JSON object. Abort." >&2
  exit 2
fi
AUTHOR_ASSOCIATION=$(printf '%s' "$PR_REST_JSON" | _lib_jq -r '.author_association // empty' 2>/dev/null) || AUTHOR_ASSOCIATION=""
if [[ -z "$AUTHOR_ASSOCIATION" ]]; then
  echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's metadata carried no author_association. Abort." >&2
  exit 2
fi
# REST snake_case, unlike the camelCase `changedFiles` of `gh pr view --json`.
if ! REST_CHANGED_FILES_TOTAL=$(review_pr_rest_changed_files "$PR_REST_JSON"); then
  echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's REST payload carried no usable changed_files count. Abort." >&2
  exit 2
fi

# files/changedFiles: `gh pr view --json files` silently caps at 100
# entries with no --paginate equivalent, and files is exactly what step 2's
# passive-execution audit reads -- a file at position 101 would be
# invisible if trusted alone. Compare files' own length against
# changedFiles and re-fetch the full list via the REST endpoint's own
# --paginate on any mismatch.
FILES_COUNT=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.files | length' 2>/dev/null) || FILES_COUNT=""
CHANGED_FILES_COUNT=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.changedFiles // empty' 2>/dev/null) || CHANGED_FILES_COUNT=""
GH_PR_PAGINATE_TIMEOUT_SECONDS=30
FILES_JSON=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -c '[.files[].path]' 2>/dev/null) || FILES_JSON="[]"
if [[ -z "$CHANGED_FILES_COUNT" || "$FILES_COUNT" != "$CHANGED_FILES_COUNT" ]]; then
  # per_page=100 is GitHub's maximum page size and the size gh --paginate requests by default.
  FILES_FETCH_STATUS=0
  RAW_FILES=$(_lib_gh "$GH_PR_PAGINATE_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/files?per_page=100" --paginate --jq "$REVIEW_PR_FILE_NAMES_JQ_FILTER" 2>/dev/null) || FILES_FETCH_STATUS=$?
  if [[ "$FILES_FETCH_STATUS" -ne 0 ]]; then
    echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's files/changedFiles counts disagree and the full re-fetch (gh api --paginate) $(review_pr_gh_status_description "$FILES_FETCH_STATUS"). Abort -- a partial or failed listing must never be treated as the full, or an empty, file set." >&2
    exit 2
  fi
  if ! FILES_JSON=$(review_pr_decode_file_names "$RAW_FILES"); then
    echo "review-pr-acquire.sh: could not decode PR $OWNER_REPO#$PR_NUMBER's re-fetched file list as JSON. Abort." >&2
    exit 2
  fi
  if ! REFETCHED_FILES_COUNT=$(review_pr_file_count_matches "$FILES_JSON" "$REST_CHANGED_FILES_TOTAL"); then
    echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's re-fetched file list has ${REFETCHED_FILES_COUNT:-an unreadable number of} entries but the PR reports $REST_CHANGED_FILES_TOTAL changed files -- the listing is truncated, or the PR changed while it was being fetched. Retry only helps in the second case; abort -- a truncated listing must never be treated as the full file set." >&2
    exit 2
  fi
fi
# Derived from the final list rather than assumed: the list taken straight
# from gh pr view can agree with `changedFiles` yet still differ from the
# REST total if the PR moved between the two calls.
if review_pr_file_count_matches "$FILES_JSON" "$REST_CHANGED_FILES_TOTAL" >/dev/null; then
  FILES_COMPLETE=true
else
  FILES_COMPLETE=false
fi

# commits: gh pr view --json commits shares files' own 100-entry cap. The
# REST payload's own `.commits` integer (the true total) is the
# reconciliation source, already fetched above alongside author_association.
COMMITS_ARRAY_COUNT=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.commits | length' 2>/dev/null) || COMMITS_ARRAY_COUNT=""
REST_COMMITS_TOTAL=$(printf '%s' "$PR_REST_JSON" | _lib_jq -r '.commits // empty' 2>/dev/null) || REST_COMMITS_TOTAL=""
COMMITS_JSON=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -c '[.commits[].oid]' 2>/dev/null) || COMMITS_JSON="[]"
if [[ -n "$REST_COMMITS_TOTAL" && "$COMMITS_ARRAY_COUNT" != "$REST_COMMITS_TOTAL" ]]; then
  COMMITS_FETCH_STATUS=0
  RAW_COMMITS=$(_lib_gh "$GH_PR_PAGINATE_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/commits?per_page=100" --paginate --jq '.[].sha' 2>/dev/null) || COMMITS_FETCH_STATUS=$?
  if [[ "$COMMITS_FETCH_STATUS" -ne 0 ]]; then
    echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's commit counts disagree and the full re-fetch (gh api --paginate) $(review_pr_gh_status_description "$COMMITS_FETCH_STATUS"). Abort -- a partial or failed listing must never be treated as the full, or an empty, commit set." >&2
    exit 2
  fi
  if ! COMMITS_JSON=$(printf '%s' "$RAW_COMMITS" | _lib_jq -R -s -c 'split("\n") | map(select(length > 0))' 2>/dev/null); then
    echo "review-pr-acquire.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's re-fetched commit list as JSON. Abort." >&2
    exit 2
  fi
fi
# Derived from the final list against the REST total, not from the
# re-fetch having run: the re-fetch can itself fall short on a very long PR.
FINAL_COMMITS_COUNT=$(printf '%s' "$COMMITS_JSON" | _lib_jq -r 'length' 2>/dev/null) || FINAL_COMMITS_COUNT=""
if [[ -n "$REST_COMMITS_TOTAL" && "$FINAL_COMMITS_COUNT" == "$REST_COMMITS_TOTAL" ]]; then
  COMMITS_COMPLETE=true
else
  COMMITS_COMPLETE=false
fi

# Existing review bodies -- what other reviewers already raised, so step 7
# doesn't repeat them. No --slurp: `--jq` applied per page (as in
# review-pr-checkout.sh's file-list fetch) streams each matching review
# object on its own line, and a local `jq -s` combines that stream into one
# JSON array, as in the files/commits re-fetch encoding above.
REVIEWS_FETCH_STATUS=0
RAW_REVIEWS=$(_lib_gh "$GH_PR_PAGINATE_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/reviews?per_page=100" --paginate --jq '.[] | select(.body != "") | {id, author: .user.login, state, body}' 2>/dev/null) || REVIEWS_FETCH_STATUS=$?
if [[ "$REVIEWS_FETCH_STATUS" -ne 0 ]]; then
  echo "review-pr-acquire.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's existing reviews (gh api --paginate $(review_pr_gh_status_description "$REVIEWS_FETCH_STATUS")). Abort." >&2
  exit 2
fi
if [[ -z "$RAW_REVIEWS" ]]; then
  REVIEWS_JSON="[]"
elif ! REVIEWS_JSON=$(printf '%s' "$RAW_REVIEWS" | _lib_jq -c -s '.' 2>/dev/null); then
  echo "review-pr-acquire.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's existing reviews as JSON. Abort." >&2
  exit 2
fi

# Existing inline review comments -- a prior reviewer who left only inline
# comments has no review body for the fetch above to return. Same
# `--jq` per-page stream and local `jq -s` combine as the review bodies.
# The endpoint's own listing cap is not established, so no completeness flag
# is derived for it.
INLINE_COMMENTS_FETCH_STATUS=0
RAW_INLINE_COMMENTS=$(_lib_gh "$GH_PR_PAGINATE_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/comments?per_page=100" --paginate --jq '.[] | {author: .user.login, path, line, body}' 2>/dev/null) || INLINE_COMMENTS_FETCH_STATUS=$?
if [[ "$INLINE_COMMENTS_FETCH_STATUS" -ne 0 ]]; then
  echo "review-pr-acquire.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's existing inline comments (gh api --paginate $(review_pr_gh_status_description "$INLINE_COMMENTS_FETCH_STATUS")). Abort." >&2
  exit 2
fi
if [[ -z "$RAW_INLINE_COMMENTS" ]]; then
  INLINE_COMMENTS_JSON="[]"
elif ! INLINE_COMMENTS_JSON=$(printf '%s' "$RAW_INLINE_COMMENTS" | _lib_jq -c -s '.' 2>/dev/null); then
  echo "review-pr-acquire.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's existing inline comments as JSON. Abort." >&2
  exit 2
fi

# The four lists go to jq through files, not argv: a single argv string is
# capped (Linux MAX_ARG_STRLEN, 128 KiB), and a large PR's file list or
# review bodies can exceed that. The directory is removed on every exit
# path by the one EXIT trap this script registers.
if ! CONTEXT_TMP_DIR=$(mktemp -d "${TMPDIR:-/tmp}/review-pr-acquire.XXXXXX"); then
  echo "review-pr-acquire.sh: could not create a temp directory for assembling the context document. Abort." >&2
  exit 2
fi
trap 'rm -rf -- "${CONTEXT_TMP_DIR:?}"' EXIT
if ! { printf '%s' "$FILES_JSON" > "$CONTEXT_TMP_DIR/files.json" \
  && printf '%s' "$COMMITS_JSON" > "$CONTEXT_TMP_DIR/commits.json" \
  && printf '%s' "$REVIEWS_JSON" > "$CONTEXT_TMP_DIR/reviews.json" \
  && printf '%s' "$INLINE_COMMENTS_JSON" > "$CONTEXT_TMP_DIR/inline-comments.json"; }; then
  echo "review-pr-acquire.sh: could not stage the context document's lists in $CONTEXT_TMP_DIR. Abort." >&2
  exit 2
fi

# shellcheck disable=SC2016 # the single-quoted jq filter below intentionally
# does not expand $prIdentity/etc: those are jq --arg bindings, not shell
# variables, and double-quoting would trigger shell expansion inside the jq
# filter instead of leaving the bindings to jq itself.
# --slurpfile wraps each file's one JSON value in an array, hence `[0]`.
# The completeness flags lead the document so a cut at the end of stdout
# keeps the flags.
if ! CONTEXT_JSON=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -c \
  --arg prIdentity "$PR_IDENTITY" \
  --arg authorAssociation "$AUTHOR_ASSOCIATION" \
  --argjson filesComplete "$FILES_COMPLETE" \
  --argjson commitsComplete "$COMMITS_COMPLETE" \
  --slurpfile files "$CONTEXT_TMP_DIR/files.json" \
  --slurpfile commits "$CONTEXT_TMP_DIR/commits.json" \
  --slurpfile existingReviews "$CONTEXT_TMP_DIR/reviews.json" \
  --slurpfile existingInlineComments "$CONTEXT_TMP_DIR/inline-comments.json" \
  '{filesComplete: $filesComplete, commitsComplete: $commitsComplete} + . + {
     statusCheckRollup: (.statusCheckRollup // []),
     prIdentity: $prIdentity,
     authorAssociation: $authorAssociation,
     files: $files[0],
     commits: $commits[0],
     existingReviews: $existingReviews[0],
     existingInlineComments: $existingInlineComments[0]
   }' 2>/dev/null); then
  echo "review-pr-acquire.sh: could not assemble PR $OWNER_REPO#$PR_NUMBER's context document. Abort." >&2
  exit 2
fi

CONTEXT_FILE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" context.json)
if ! CONTEXT_FILE_JSON=$(printf '%s' "$CONTEXT_JSON" | _lib_jq . 2>/dev/null) \
  || ! printf '%s\n' "$CONTEXT_FILE_JSON" | _lib_write_no_follow "$CONTEXT_FILE"; then
  echo "review-pr-acquire.sh: could not write the backstop context file $CONTEXT_FILE. Abort." >&2
  exit 2
fi

# Provenance write, mode "acquired" (the usage text names the later rewrites).
PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)
if ! _lib_write_review_pr_provenance "$PROVENANCE" \
  "pr_identity=$PR_IDENTITY" "head_ref_oid=$HEAD_REF_OID" "pid=$CLAUDE_PID" "mode=acquired"; then
  echo "review-pr-acquire.sh: could not write provenance file $PROVENANCE. Abort." >&2
  exit 2
fi

# The path line precedes the document so a head-truncated merged stream keeps it.
echo "review-pr-acquire.sh: context backstop file (Read it if stdout was cut off): $CONTEXT_FILE" >&2
printf '%s\n' "$CONTEXT_JSON"
