#!/usr/bin/env bash
# Post a /review-pr review. The verdict is the script's first argument;
# --approve is not constructible from it. The second argument names the
# PR the caller believes it is posting to. See docs/hooks.md's
# require-respond-pr.sh entry for the gate that redirects here.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-post.sh <comment|request-changes> <owner>/<repo>#<number>

Posts the /review-pr findings body recorded by this session's `marker.sh
write review-pr` completion marker, as the named gh pr review verdict.
Before posting, verifies: a completion marker exists for this repo (keyed
to its main tree root, so any tree of it resolves the same marker) and
session; the marker's recorded mode is checkout or diff-only; the marker's
recorded PR identity is a valid <owner>/<repo>#<number>; the target argument
equals both that identity and this repo's origin remote (owner/repo); the
findings-body file is readable and its sha256 still equals
the marker's recorded hash; and the marker's PR number/owner/repo names a
real PR whose current headRefOid still matches the marker's recorded HEAD.
Fails closed (no gh call) on any missing or mismatched piece. The marker is
consumed before the post call, and a failure to consume it refuses before
any post: gh pr review has no idempotency key, so a retry after a post that
landed, or that failed after landing, could double-post. A post that fails
therefore leaves it unknown whether the review landed.
EOF
}

if [[ $# -ne 2 ]]; then
  usage
  exit 2
fi

# Validated up front so a bad verdict fails before any of the marker/HEAD/
# body-hash work below runs. Re-checked in the case at the bottom, which is
# the only place a `gh pr review` call is constructed.
case "$1" in
  comment | request-changes) ;;
  *)
    usage
    exit 2
    ;;
esac

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

if ! TARGET_FIELDS=$(_lib_parse_pr_identity "$2"); then
  echo "review-pr-post.sh: target '$2' is not a valid <owner>/<repo>#<number>." >&2
  usage
  exit 2
fi
TARGET_OWNER_REPO=$(printf '%s\n' "$TARGET_FIELDS" | sed -n '1p')

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-post.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty). Abort without posting." >&2
  exit 2
}

SESSION_ID=$("$(dirname "$0")/marker.sh" resolve-session-id) || {
  echo "review-pr-post.sh: could not resolve this session's id. Abort without posting." >&2
  exit 2
}

REPO_HASH=$(_lib_review_pr_marker_repo_hash) || {
  echo "review-pr-post.sh: not inside a git repository, git is older than 2.31 (which lacks rev-parse --path-format), or the repo hash could not be computed. Abort without posting." >&2
  exit 2
}

MARKER_PATH="$CONFIG_DIR/review-pr-markers/$REPO_HASH.$SESSION_ID"
MARKER_FIELDS=$(_lib_review_pr_completion_marker_fields "$CONFIG_DIR" "$REPO_HASH" "$SESSION_ID") || {
  echo "review-pr-post.sh: no /review-pr completion marker for this repo and session -- if no post has been attempted, run the skill through Step 7 before posting. If a previous post attempt ran, it consumed the marker and this session must not post that body again: ask the human to check the PR for its review. Abort without posting." >&2
  exit 2
}
MARKER_PR_IDENTITY=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '1p')
MARKER_HEAD_REF_OID=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '2p')
MARKER_BODY_HASH=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '3p')
MARKER_MODE=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '4p')

# The remote headRefOid re-check further below is the sole freshness
# binding in both modes. An out-of-enum mode (a corrupted or hand-written
# provenance file -- any process that can write files can write this
# skill's own state) refuses rather than falling through to either known
# branch by default.
case "$MARKER_MODE" in
  checkout | diff-only) ;;
  *)
    echo "review-pr-post.sh: completion marker mode '$MARKER_MODE' is neither checkout nor diff-only. Abort without posting." >&2
    exit 2
    ;;
esac

# See _lib_parse_pr_identity (_lib.sh) for the split and validation.
if ! PR_IDENTITY_FIELDS=$(_lib_parse_pr_identity "$MARKER_PR_IDENTITY"); then
  echo "review-pr-post.sh: marker PR identity '$MARKER_PR_IDENTITY' is not a valid <owner>/<repo>#<number>. Abort without posting." >&2
  exit 2
fi
OWNER_REPO=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '1p')
PR_NUMBER=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '2p')

# The target names the PR the caller means to post to. It must match both the
# marker's own recorded PR and the repository this checkout's origin points
# at, so a review the marker vouches for is never posted to a PR the caller
# did not name, nor to a repo other than the one being worked in.
if _lib_case_insensitive_ne "$2" "$MARKER_PR_IDENTITY"; then
  echo "review-pr-post.sh: target '$2' does not match the completion marker's PR identity '$MARKER_PR_IDENTITY'. Abort without posting." >&2
  exit 2
fi
ORIGIN_OWNER_REPO=$(_lib_origin_owner_repo) || {
  echo "review-pr-post.sh: could not resolve this repository's origin remote, or parse an owner/repo out of its URL. Abort without posting." >&2
  exit 2
}
if _lib_case_insensitive_ne "$ORIGIN_OWNER_REPO" "$TARGET_OWNER_REPO"; then
  echo "review-pr-post.sh: target '$2' names repo '$TARGET_OWNER_REPO', which does not match this repository's origin remote ('$ORIGIN_OWNER_REPO'). Abort without posting." >&2
  exit 2
fi

# Calls the same shared _lib_review_pr_artifact_path helper (_lib.sh) that
# marker.sh's `write review-pr` arm calls, at the same fixed suffix --
# SKILL.md's synthesize-and-record step writes the findings body here and
# nowhere else.
FINDINGS_BODY_PATH=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" body)

# One read supplies both the hash and the bytes posted below, so a change to
# the file after this read cannot change what is posted.
# The `x` sentinel keeps trailing newlines through the command substitution.
# A NUL byte in the file is dropped by the substitution, which makes the hash
# differ from the marker's and refuses.
if ! FINDINGS_BODY=$(_lib_capped cat -- "$FINDINGS_BODY_PATH" 2>/dev/null && printf x); then
  echo "review-pr-post.sh: findings-body file $FINDINGS_BODY_PATH is missing or unreadable. Abort without posting." >&2
  exit 2
fi
FINDINGS_BODY=${FINDINGS_BODY%x}
ACTUAL_BODY_HASH=$(_lib_hash_diff_text "$FINDINGS_BODY") || ACTUAL_BODY_HASH=""
if [[ -z "$ACTUAL_BODY_HASH" || "$ACTUAL_BODY_HASH" != "$MARKER_BODY_HASH" ]]; then
  echo "review-pr-post.sh: findings-body file $FINDINGS_BODY_PATH no longer matches the reviewed hash (or could not be hashed). Abort without posting." >&2
  exit 2
fi

# PR_NUMBER/OWNER_REPO are validated above by shape only -- neither check
# proves this number actually names the PR the marker's body-hash check
# above was run against. Re-fetch the PR's own current headRefOid
# and compare it to the marker's recorded value: a mismatch means
# PR_NUMBER/OWNER_REPO does not name the reviewed PR, so abort before
# constructing a review against the wrong one.
#
# 10s: a network GET carrying no payload, more slack than _lib_capped's 5s
# local-read default but less than the write-path headroom
# GH_PR_REVIEW_TIMEOUT_SECONDS gives the POST calls below.
GH_PR_VIEW_TIMEOUT_SECONDS=10
CURRENT_PR_HEAD=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || CURRENT_PR_HEAD=""
if [[ -z "$CURRENT_PR_HEAD" || "$CURRENT_PR_HEAD" != "$MARKER_HEAD_REF_OID" ]]; then
  echo "review-pr-post.sh: PR $OWNER_REPO#$PR_NUMBER's current headRefOid does not match the completion marker's recorded HEAD -- PR_NUMBER/OWNER_REPO may not name the reviewed PR. Abort without posting." >&2
  exit 2
fi

# The marker is consumed before the post: a gh pr review POST has no
# idempotency key, so a stop at any later point (a kill, a timeout after the
# request landed) must leave the marker gone, and a retry then fails closed at
# the "no completion marker" check above instead of double-posting.
# unlink exits non-zero when the marker cannot be removed, which refuses before
# any post. Not `rm`: it prompts about a write-protected file on a terminal, and
# a declined prompt exits 0 without removing anything.
if ! unlink -- "$MARKER_PATH"; then
  echo "review-pr-post.sh: could not consume the completion marker $MARKER_PATH. Abort without posting." >&2
  exit 2
fi

# The two `gh pr review` calls below are the only ones in this script, each
# with a literal verdict flag never built from $1. --approve cannot appear
# here.
#
# 20s, not _lib_capped's 5s local-read default: this call is a network POST
# carrying a body, not a local git/index read, so it needs more slack than a
# request with no payload.
GH_PR_REVIEW_TIMEOUT_SECONDS=20
# The body goes to gh on stdin (`-F -`) from the same bytes that were hashed.
# gh-pr-review(1), v2.100.0, for -F/--body-file: use "-" to read from standard input.
# A failing call is captured, not left to `set -e`, so the failure message
# below prints.
POST_STATUS=0
case "$1" in
  comment)
    printf '%s' "$FINDINGS_BODY" | _lib_gh "$GH_PR_REVIEW_TIMEOUT_SECONDS" pr review "$PR_NUMBER" --comment -R "$OWNER_REPO" -F - || POST_STATUS=$?
    ;;
  request-changes)
    printf '%s' "$FINDINGS_BODY" | _lib_gh "$GH_PR_REVIEW_TIMEOUT_SECONDS" pr review "$PR_NUMBER" --request-changes -R "$OWNER_REPO" -F - || POST_STATUS=$?
    ;;
  *)
    exit 2
    ;;
esac

if [[ "$POST_STATUS" -ne 0 ]]; then
  echo "review-pr-post.sh: gh pr review exited $POST_STATUS (failed or timed out), so whether the review posted is unknown. The completion marker was consumed before the post, so this session must not post that body again. Ask the human to check PR $OWNER_REPO#$PR_NUMBER on GitHub and, if the review is absent, post the reviewed body by hand or start a fresh /review-pr." >&2
  exit 2
fi
