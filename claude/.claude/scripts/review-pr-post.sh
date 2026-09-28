#!/usr/bin/env bash
# Post a /review-pr review. The verdict is the script's only argument;
# --approve is not constructible from it. See docs/hooks.md's
# require-respond-pr.sh entry for the gate that redirects here.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-post.sh <comment|request-changes>

Posts the /review-pr findings body recorded by this session's `marker.sh
write review-pr` completion marker, as the named gh pr review verdict.
Before posting, verifies: a completion marker exists for this repo and
session; the worktree's current HEAD still equals the marker's recorded
headRefOid; the findings-body file's sha256 still equals the marker's
recorded hash; and the marker's PR number/owner/repo names a real PR whose
current headRefOid still matches the marker's recorded HEAD. Fails closed
(no gh call) on any missing or mismatched piece.
EOF
}

if [[ $# -ne 1 ]]; then
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

# Single combined EXIT trap (shell-script-conventions.md): cleans up the
# verified-content temp file created below, on every exit path including an
# abort before it exists.
TMP_FINDINGS_BODY_FILE=""
# shellcheck disable=SC2329 # invoked indirectly via the trap registered below, which shellcheck's static analysis doesn't follow.
_cleanup_tmp_findings_body() {
  # Captures and restores $? explicitly: under `set -e`, an EXIT trap whose
  # last command is a false `[[ -n ... ]]` test would otherwise overwrite the
  # script's real exit code with that test's own failure status.
  local exit_code=$?
  [[ -n "$TMP_FINDINGS_BODY_FILE" ]] && rm -f -- "$TMP_FINDINGS_BODY_FILE"
  return "$exit_code"
}
trap _cleanup_tmp_findings_body EXIT

CONFIG_DIR=$(_lib_config_dir) || {
  echo "review-pr-post.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty). Abort without posting." >&2
  exit 2
}

SESSION_ID=$("$(dirname "$0")/marker.sh" resolve-session-id) || {
  echo "review-pr-post.sh: could not resolve this session's id. Abort without posting." >&2
  exit 2
}

REPO_ROOT=$(_lib_capped git rev-parse --show-toplevel 2>/dev/null) || REPO_ROOT=""
if [[ -z "$REPO_ROOT" ]]; then
  echo "review-pr-post.sh: not inside a git repository. Abort without posting." >&2
  exit 2
fi
REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT") || {
  echo "review-pr-post.sh: could not compute the repo hash. Abort without posting." >&2
  exit 2
}

MARKER_FIELDS=$(_lib_review_pr_completion_marker_fields "$CONFIG_DIR" "$REPO_HASH" "$SESSION_ID") || {
  echo "review-pr-post.sh: no /review-pr completion marker for this repo and session -- run the skill through Step 7 before posting. Abort without posting." >&2
  exit 2
}
MARKER_PR_IDENTITY=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '1p')
MARKER_HEAD_REF_OID=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '2p')
MARKER_BODY_HASH=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '3p')
MARKER_MODE=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '4p')

# The local HEAD comparison only makes sense in `checkout` mode, where a
# reviewed tree actually exists -- in `diff-only` mode there is no local
# tree to compare, and the remote headRefOid re-check further below is the
# sole freshness binding. An out-of-enum mode (a corrupted or hand-written
# provenance file -- any process that can write files can write this
# skill's own state) refuses rather than falling through to either known
# branch by default.
case "$MARKER_MODE" in
  checkout)
    CURRENT_HEAD=$(_lib_capped git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null) || CURRENT_HEAD=""
    if [[ -z "$CURRENT_HEAD" || "$CURRENT_HEAD" != "$MARKER_HEAD_REF_OID" ]]; then
      echo "review-pr-post.sh: worktree HEAD does not match the reviewed headRefOid recorded by the completion marker -- the diff moved since the review ran. Abort without posting." >&2
      exit 2
    fi
    ;;
  diff-only) ;;
  *)
    echo "review-pr-post.sh: completion marker mode '$MARKER_MODE' is neither checkout nor diff-only. Abort without posting." >&2
    exit 2
    ;;
esac

# Calls the same shared _lib_review_pr_artifact_path helper (_lib.sh) that
# marker.sh's `write review-pr` arm calls, at the same fixed suffix --
# SKILL.md Step 7 writes the findings body here and nowhere else.
FINDINGS_BODY_PATH=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" body)

# _lib_cat_no_follow reads through a single O_NOFOLLOW open, matching
# marker.sh's own hardened read of this same file: a separate `[ -L ]` check
# followed by a later, ordinary open is not atomic. The hash below is
# computed from this same captured content, never a second open of
# FINDINGS_BODY_PATH by path -- `gh -F`'s own read further down is an
# ordinary, symlink-following open, so re-opening this path again after the
# check below would leave a symlink-swap window between the check and that
# later read. The trailing 'x' sentinel, stripped back off, preserves a
# trailing newline command substitution would otherwise strip, so the hash
# matches what _lib_sha256_no_follow would compute directly on the file.
FINDINGS_BODY_CONTENT=$(_lib_cat_no_follow "$FINDINGS_BODY_PATH" 2>/dev/null; printf x)
FINDINGS_BODY_CONTENT="${FINDINGS_BODY_CONTENT%x}"
ACTUAL_BODY_HASH=$(_lib_hash_diff_text "$FINDINGS_BODY_CONTENT") || ACTUAL_BODY_HASH=""
if [[ -z "$ACTUAL_BODY_HASH" || "$ACTUAL_BODY_HASH" != "$MARKER_BODY_HASH" ]]; then
  echo "review-pr-post.sh: findings-body file $FINDINGS_BODY_PATH is missing, unreadable, a symlink, or no longer matches the reviewed hash. Abort without posting." >&2
  exit 2
fi

# Verified content written to our own mktemp file (not FINDINGS_BODY_PATH
# itself) for `gh -F` to read -- see the comment above for why the file at
# FINDINGS_BODY_PATH must not be opened again after the hash check.
TMP_FINDINGS_BODY_FILE=$(mktemp -t review-pr-findings-body.XXXXXX) || {  # GNU mktemp requires the XXXXXX suffix; a bare prefix is BSD-only.
  echo "review-pr-post.sh: could not create a temp file for the verified findings body. Abort without posting." >&2
  exit 2
}
printf '%s' "$FINDINGS_BODY_CONTENT" > "$TMP_FINDINGS_BODY_FILE"

# Same split and validation review-pr-checkout.sh's own PR-identity
# handling uses, so the two scripts agree on one PR-identity convention.
if ! PR_IDENTITY_FIELDS=$(_lib_parse_pr_identity "$MARKER_PR_IDENTITY"); then
  echo "review-pr-post.sh: marker PR identity '$MARKER_PR_IDENTITY' is not a valid <owner>/<repo>#<number>. Abort without posting." >&2
  exit 2
fi
OWNER_REPO=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '1p')
PR_NUMBER=$(printf '%s\n' "$PR_IDENTITY_FIELDS" | sed -n '2p')

# PR_NUMBER/OWNER_REPO are validated above by shape only -- neither check
# proves this number actually names the PR the marker's HEAD/body-hash
# checks above were run against. Re-fetch the PR's own current headRefOid
# and compare it to the marker's recorded value: a mismatch means
# PR_NUMBER/OWNER_REPO does not name the reviewed PR, so abort before
# constructing a review against the wrong one.
#
# 10s: a network GET carrying no payload, more slack than _lib_capped's 5s
# local-read default but less than the write-path headroom
# GH_PR_REVIEW_TIMEOUT_SECONDS gives the POST calls below.
# GH_HOST/GH_ENTERPRISE_TOKEN stripped via _lib_gh for the same reason as
# those calls: an ambient GH_HOST would otherwise let adversarial PR
# content redirect even this identity check to a different host.
GH_PR_VIEW_TIMEOUT_SECONDS=10
CURRENT_PR_HEAD=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || CURRENT_PR_HEAD=""
if [[ -z "$CURRENT_PR_HEAD" || "$CURRENT_PR_HEAD" != "$MARKER_HEAD_REF_OID" ]]; then
  echo "review-pr-post.sh: PR $OWNER_REPO#$PR_NUMBER's current headRefOid does not match the completion marker's recorded HEAD -- PR_NUMBER/OWNER_REPO may not name the reviewed PR. Abort without posting." >&2
  exit 2
fi

# The two `gh pr review` calls below are the only ones in this script, each
# with a literal verdict flag never built from $1. --approve cannot appear
# here.
#
# 20s, not _lib_capped's 5s local-read default: this call is a network POST
# carrying a body file, not a local git/index read, so it needs more slack
# than a request with no payload.
GH_PR_REVIEW_TIMEOUT_SECONDS=20
# GH_HOST/GH_ENTERPRISE_TOKEN stripped via _lib_gh from gh's environment on
# both calls: an ambient GH_HOST (adversarial PR content could induce the
# calling agent to set one) would otherwise silently redirect the post to a
# different host before this script's own checks have any say in it.
case "$1" in
  comment)
    if ! _lib_gh "$GH_PR_REVIEW_TIMEOUT_SECONDS" pr review "$PR_NUMBER" --comment -R "$OWNER_REPO" -F "$TMP_FINDINGS_BODY_FILE"; then
      echo "review-pr-post.sh: gh pr review --comment failed or timed out. Completion marker left intact for a retry." >&2
      exit 2
    fi
    ;;
  request-changes)
    if ! _lib_gh "$GH_PR_REVIEW_TIMEOUT_SECONDS" pr review "$PR_NUMBER" --request-changes -R "$OWNER_REPO" -F "$TMP_FINDINGS_BODY_FILE"; then
      echo "review-pr-post.sh: gh pr review --request-changes failed or timed out. Completion marker left intact for a retry." >&2
      exit 2
    fi
    ;;
  *)
    exit 2
    ;;
esac

# Self-consuming: a gh pr review POST has no idempotency key, so a retry
# after a successful post would double-post. Deleting the completion marker
# here makes a subsequent invocation fail closed at the "no completion
# marker" check above instead of re-posting.
rm -f "$CONFIG_DIR/review-pr-markers/$REPO_HASH.$SESSION_ID"
