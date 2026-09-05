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
headRefOid; and the findings-body file's sha256 still equals the marker's
recorded hash. Fails closed (no gh call) on any missing or mismatched
piece.
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
REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")

MARKER_FIELDS=$(_lib_review_pr_completion_marker_fields "$CONFIG_DIR" "$REPO_HASH" "$SESSION_ID") || {
  echo "review-pr-post.sh: no /review-pr completion marker for this repo and session -- run the skill through Step 8 before posting. Abort without posting." >&2
  exit 2
}
MARKER_PR_IDENTITY=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '1p')
MARKER_HEAD_REF_OID=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '2p')
MARKER_BODY_HASH=$(printf '%s\n' "$MARKER_FIELDS" | sed -n '3p')

CURRENT_HEAD=$(_lib_capped git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null) || CURRENT_HEAD=""
if [[ -z "$CURRENT_HEAD" || "$CURRENT_HEAD" != "$MARKER_HEAD_REF_OID" ]]; then
  echo "review-pr-post.sh: worktree HEAD does not match the reviewed headRefOid recorded by the completion marker -- the diff moved since the review ran. Abort without posting." >&2
  exit 2
fi

# Same fixed-path derivation as marker.sh's own
# _review_pr_findings_body_fixed_path -- SKILL.md Step 8 writes the findings
# body here and nowhere else.
FINDINGS_BODY_PATH="$CONFIG_DIR/.review-pr-active.d/$SESSION_ID.body"

# O_NOFOLLOW read, matching marker.sh's own hardened read of this same file:
# a separate `[ -L ]` check followed by sha256sum is not atomic, so read
# through a single open that refuses a symlink at the final path component.
ACTUAL_BODY_HASH=$(_lib_capped python3 -c '
import hashlib, os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
except OSError:
    sys.exit(1)
digest = hashlib.sha256()
with os.fdopen(fd, "rb") as f:
    for chunk in iter(lambda: f.read(65536), b""):
        digest.update(chunk)
print(digest.hexdigest())
' "$FINDINGS_BODY_PATH" 2>/dev/null) || ACTUAL_BODY_HASH=""
if [[ -z "$ACTUAL_BODY_HASH" || "$ACTUAL_BODY_HASH" != "$MARKER_BODY_HASH" ]]; then
  echo "review-pr-post.sh: findings-body file $FINDINGS_BODY_PATH is missing, unreadable, a symlink, or no longer matches the reviewed hash. Abort without posting." >&2
  exit 2
fi

# PR_IDENTITY is <owner>/<repo>#<number>, split with the same
# parameter-expansion pattern as _lib_review_pr_completion_marker_fields in
# _lib.sh.
PR_NUMBER="${MARKER_PR_IDENTITY##*#}"
OWNER_REPO="${MARKER_PR_IDENTITY%#*}"
if [[ ! "$PR_NUMBER" =~ ^[0-9]+$ ]]; then
  echo "review-pr-post.sh: marker PR identity '$MARKER_PR_IDENTITY' has no numeric PR number. Abort without posting." >&2
  exit 2
fi
if [[ ! "$OWNER_REPO" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]; then
  echo "review-pr-post.sh: marker PR identity '$MARKER_PR_IDENTITY' does not name a valid owner/repo. Abort without posting." >&2
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
# GH_HOST/GH_ENTERPRISE_TOKEN stripped from gh's environment on both calls:
# an ambient GH_HOST (adversarial PR content could induce the calling agent
# to set one) would otherwise silently redirect the post to a different
# host before this script's own checks have any say in it.
case "$1" in
  comment)
    if ! _lib_capped_for "$GH_PR_REVIEW_TIMEOUT_SECONDS" env -u GH_HOST -u GH_ENTERPRISE_TOKEN gh pr review "$PR_NUMBER" --comment -R "$OWNER_REPO" -F "$FINDINGS_BODY_PATH"; then
      echo "review-pr-post.sh: gh pr review --comment failed or timed out. Completion marker left intact for a retry." >&2
      exit 2
    fi
    ;;
  request-changes)
    if ! _lib_capped_for "$GH_PR_REVIEW_TIMEOUT_SECONDS" env -u GH_HOST -u GH_ENTERPRISE_TOKEN gh pr review "$PR_NUMBER" --request-changes -R "$OWNER_REPO" -F "$FINDINGS_BODY_PATH"; then
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
