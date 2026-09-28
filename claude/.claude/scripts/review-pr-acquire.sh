#!/usr/bin/env bash
# The acquire step for /review-pr: one script call replacing a hand-typed
# sequence of `gh` calls, so the pagination reconciliation and the
# review-thread fetch are performed the same way on every run rather than
# left to the model's own transcription. Needs no active-bypass marker of
# its own: require-respond-pr.sh matches only the literal Bash-tool command
# text, which is `~/.claude/scripts/review-pr-acquire.sh <owner>/<repo>#<N>`
# here -- it never sees the `gh api .../reviews` call this script makes
# internally, the same gap require-worktree-for-git-writes.sh has against a
# wrapper script's own internal calls (see docs/hooks.md). require-respond-pr.sh's
# ungated release of this invocation depends on that gap staying true: this
# script must never make a write-shaped `gh api`/`gh pr`/etc. call.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-acquire.sh <owner>/<repo>#<number>

Fetches everything /review-pr's later steps need in one call: gh pr view's
own metadata fields, author_association (a separate REST call -- not a
valid `gh pr view --json` field), the full paginated changed-files and
commits lists (re-fetched via --paginate when gh pr view's own capped
fields disagree with the PR's real totals), gh pr checks, and existing
review bodies. Prints one JSON document on stdout and writes the identical
document to $CONFIG_DIR/.review-pr-active.d/$SESSION_ID.context.json, a
backstop against a harness-truncated stdout on a large PR. Also writes this
session's provenance file (mode "acquired") -- review-pr-checkout.sh and
review-pr-diff.sh each rewrite it (mode "checkout"/"diff-only") after their
own independent re-derivation; marker.sh write review-pr accepts only
those two modes, so an acquire-only session can never write a completion
marker. Any gh failure aborts with no partial document written, and never
echoes gh's own stderr/error text verbatim -- an API error payload can echo
request parameters, so only this script's own fixed messages reach stdout/
stderr.
EOF
}

if [[ $# -ne 1 ]]; then
  usage
  exit 2
fi

PR_IDENTITY="$1"

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

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
mkdir -p -- "$ACTIVE_DIR"

# GH_HOST/GH_ENTERPRISE_TOKEN stripped from every gh call below via
# _lib_gh -- same reasoning as review-pr-checkout.sh/review-pr-post.sh:
# adversarial PR content could induce the calling agent to set GH_HOST
# ambiently, silently redirecting a fact this script derives to an
# attacker-chosen host.

# 10s: a network GET carrying no payload, matching review-pr-checkout.sh's
# own budget for the same call shape.
GH_PR_VIEW_TIMEOUT_SECONDS=10
PR_VIEW_FIELDS="title,body,author,isCrossRepository,baseRefOid,headRefOid,headRepositoryOwner,files,changedFiles,commits,reviews,reviewDecision,mergeable,mergeStateStatus"
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
# capped array), so it doubles as the commits-reconciliation source below.
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
  if ! RAW_FILES=$(_lib_gh "$GH_PR_PAGINATE_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/files" --paginate --jq '.[].filename' 2>/dev/null); then
    echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's files/changedFiles counts disagree and the full re-fetch (gh api --paginate) failed or timed out. Abort -- a partial or failed listing must never be treated as the full, or an empty, file set." >&2
    exit 2
  fi
  if ! FILES_JSON=$(printf '%s' "$RAW_FILES" | _lib_jq -R -s -c 'split("\n") | map(select(length > 0))' 2>/dev/null); then
    echo "review-pr-acquire.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's re-fetched file list as JSON. Abort." >&2
    exit 2
  fi
fi

# commits: gh pr view --json commits shares files' own 100-entry cap. The
# REST payload's own `.commits` integer (the true total) is the
# reconciliation source, already fetched above alongside author_association.
COMMITS_ARRAY_COUNT=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -r '.commits | length' 2>/dev/null) || COMMITS_ARRAY_COUNT=""
REST_COMMITS_TOTAL=$(printf '%s' "$PR_REST_JSON" | _lib_jq -r '.commits // empty' 2>/dev/null) || REST_COMMITS_TOTAL=""
COMMITS_JSON=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -c '[.commits[].oid]' 2>/dev/null) || COMMITS_JSON="[]"
if [[ -n "$REST_COMMITS_TOTAL" && "$COMMITS_ARRAY_COUNT" != "$REST_COMMITS_TOTAL" ]]; then
  if ! RAW_COMMITS=$(_lib_gh "$GH_PR_PAGINATE_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/commits" --paginate --jq '.[].sha' 2>/dev/null); then
    echo "review-pr-acquire.sh: PR $OWNER_REPO#$PR_NUMBER's commit counts disagree and the full re-fetch (gh api --paginate) failed or timed out. Abort -- a partial or failed listing must never be treated as the full, or an empty, commit set." >&2
    exit 2
  fi
  if ! COMMITS_JSON=$(printf '%s' "$RAW_COMMITS" | _lib_jq -R -s -c 'split("\n") | map(select(length > 0))' 2>/dev/null); then
    echo "review-pr-acquire.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's re-fetched commit list as JSON. Abort." >&2
    exit 2
  fi
fi

if ! CHECKS_JSON=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr checks "$PR_NUMBER" -R "$OWNER_REPO" --json name,state,bucket,link,description,workflow 2>/dev/null); then
  echo "review-pr-acquire.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's checks (gh pr checks failed or timed out). Abort." >&2
  exit 2
fi

# Existing review bodies -- what other reviewers already raised, so step 7
# doesn't repeat them. Needs no active-bypass marker (see this script's
# header comment): require-respond-pr.sh never sees this internal gh api
# call. No --slurp: `--jq` applied per page (the same proven pattern
# review-pr-checkout.sh's own file-list fetch already uses) streams each
# matching review object on its own line; a local `jq -s` afterward
# combines that stream into one JSON array, same as the files/commits
# re-fetch encoding steps above.
if ! RAW_REVIEWS=$(_lib_gh "$GH_PR_PAGINATE_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/reviews" --paginate --jq '.[] | select(.body != "") | {id, author: .user.login, state, body}' 2>/dev/null); then
  echo "review-pr-acquire.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's existing reviews (gh api --paginate failed or timed out). Abort." >&2
  exit 2
fi
if [[ -z "$RAW_REVIEWS" ]]; then
  REVIEWS_JSON="[]"
elif ! REVIEWS_JSON=$(printf '%s' "$RAW_REVIEWS" | _lib_jq -c -s '.' 2>/dev/null); then
  echo "review-pr-acquire.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's existing reviews as JSON. Abort." >&2
  exit 2
fi

# shellcheck disable=SC2016 # the single-quoted jq filter below intentionally
# does not expand $prIdentity/etc: those are jq --arg bindings, not shell
# variables, and double-quoting would trigger shell expansion inside the jq
# filter instead of leaving the bindings to jq itself.
if ! CONTEXT_JSON=$(printf '%s' "$PR_VIEW_JSON" | _lib_jq -c \
  --arg prIdentity "$PR_IDENTITY" \
  --arg authorAssociation "$AUTHOR_ASSOCIATION" \
  --argjson files "$FILES_JSON" \
  --argjson commits "$COMMITS_JSON" \
  --argjson checks "$CHECKS_JSON" \
  --argjson existingReviews "$REVIEWS_JSON" \
  '. + {
     prIdentity: $prIdentity,
     authorAssociation: $authorAssociation,
     files: $files,
     commits: $commits,
     checks: $checks,
     existingReviews: $existingReviews
   }' 2>/dev/null); then
  echo "review-pr-acquire.sh: could not assemble PR $OWNER_REPO#$PR_NUMBER's context document. Abort." >&2
  exit 2
fi

CONTEXT_FILE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" context.json)
if ! printf '%s\n' "$CONTEXT_JSON" | _lib_write_no_follow "$CONTEXT_FILE"; then
  echo "review-pr-acquire.sh: could not write the backstop context file $CONTEXT_FILE. Abort." >&2
  exit 2
fi

# Provenance write, mode "acquired": review-pr-checkout.sh/review-pr-diff.sh
# each rewrite this same sibling file (mode "checkout"/"diff-only") after
# their own independent re-derivation. marker.sh write review-pr accepts
# only those two modes.
PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)
if ! _lib_write_review_pr_provenance "$PROVENANCE" \
  "pr_identity=$PR_IDENTITY" "head_ref_oid=$HEAD_REF_OID" "pid=$CLAUDE_PID" "mode=acquired"; then
  echo "review-pr-acquire.sh: could not write provenance file $PROVENANCE. Abort." >&2
  exit 2
fi

printf '%s\n' "$CONTEXT_JSON"
