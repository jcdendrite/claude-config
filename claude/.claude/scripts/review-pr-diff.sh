#!/usr/bin/env bash
# No-checkout review path for /review-pr's checkout-or-diff-only branch --
# review-pr-checkout.sh's trust block otherwise makes a cross-repo or
# first-time-contributor PR permanently unreviewable, since that class is
# exactly the common case on a public repo this skill exists to review.
# Mirrors review-pr-checkout.sh's self-derivation discipline (own
# PR-identity parse, own origin-identity check, own paginated file list,
# own double headRefOid fetch bracketing the pagination) minus everything
# that only matters once code lands on disk: no symlink scan (a symlink is
# just a mode-120000 diff line here, reviewable as text, never checked
# out), and no worktree.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-diff.sh <owner>/<repo>#<number>

Self-derives the PR's own file list and headRefOid, the same discipline
review-pr-checkout.sh uses, but never checks the PR out. First checks that
<owner>/<repo> matches this worktree's own origin remote, aborting before
any gh call on a mismatch. Fetches the PR's own current headRefOid and full,
paginated file list directly from `gh`, re-fetches headRefOid once more to
catch a force-push landing while the file list was being paginated, and
aborts on drift. Pipes the file list to audit-execution-surface.py, but a
hit is reported on stderr as a mandatory pre-seeded finding rather than a
stop -- nothing is landing on disk here for that predicate to protect, and
a PR touching .claude/hooks/** or .mcp.json is precisely what an inbound
reviewer must flag. Fetches `gh pr diff` and writes it to
$CONFIG_DIR/.review-pr-active.d/$SESSION_ID.diff, comparing the number of
`diff --git` headers in that output against the paginated file count as a
truncation check -- not a vendor-documented limit, a defensive heuristic
against a possibly-truncated diff response -- and reports a mismatch on
stderr rather than trusting the diff blindly. On success, rewrites this
session's provenance file with mode "diff-only" and prints the diff file's
path on stdout as the sole output of a successful run.
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

SESSION_AND_PID=$(_lib_resolve_claude_pid) || {
  echo "review-pr-diff.sh: could not resolve this session's id (capture-session-id.sh SessionStart hook did not run). Abort before any fetch." >&2
  exit 2
}
SESSION_ID="${SESSION_AND_PID%% *}"
CLAUDE_PID="${SESSION_AND_PID##* }"
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  echo "review-pr-diff.sh: resolved session id '$SESSION_ID' is not a valid path component. Abort before any fetch." >&2
  exit 2
fi

REPO_ROOT=$(_lib_capped git rev-parse --show-toplevel 2>/dev/null) || REPO_ROOT=""
if [[ -z "$REPO_ROOT" ]]; then
  echo "review-pr-diff.sh: not inside a git repository. Abort before any fetch." >&2
  exit 2
fi

# Same cross-repo audit-substitution reasoning as review-pr-checkout.sh's
# own origin check (see that script's header comment): a PR's headRefOid is
# content-addressed, so an unchecked mismatch would let this script's audit
# and diff run against a decoy repo's manufactured content while claiming
# to describe the real PR.
ORIGIN_OWNER_REPO=$(_lib_origin_owner_repo "$REPO_ROOT") || {
  echo "review-pr-diff.sh: could not resolve this worktree's origin remote, or parse an owner/repo out of its URL. Abort before any fetch." >&2
  exit 2
}
# Case-insensitive: GitHub treats owner/repo slugs case-insensitively, same
# reasoning as review-pr-checkout.sh's own identical check.
if _lib_case_insensitive_ne "$ORIGIN_OWNER_REPO" "$OWNER_REPO"; then
  echo "review-pr-diff.sh: PR identity '$PR_IDENTITY' names repo '$OWNER_REPO', which does not match this worktree's own origin remote ('$ORIGIN_OWNER_REPO'). Abort before any fetch." >&2
  exit 2
fi

AUDIT_SCRIPT="$CONFIG_DIR/skills/review-pr/audit-execution-surface.py"
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "review-pr-diff.sh: audit script not found at $AUDIT_SCRIPT -- the review-pr skill is not installed under this config dir. Abort before any fetch." >&2
  exit 2
fi

# GH_HOST/GH_ENTERPRISE_TOKEN stripped from every gh call below via
# _lib_gh, same reasoning as review-pr-checkout.sh.

GH_PR_VIEW_TIMEOUT_SECONDS=10
HEAD_REF_OID=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || HEAD_REF_OID=""
if [[ -z "$HEAD_REF_OID" ]]; then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's current headRefOid. Abort." >&2
  exit 2
fi

GH_PR_FILES_TIMEOUT_SECONDS=30
if ! RAW_FILES=$(_lib_gh "$GH_PR_FILES_TIMEOUT_SECONDS" api "repos/$OWNER_REPO/pulls/$PR_NUMBER/files" --paginate --jq '.[].filename' 2>/dev/null); then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's file list (gh api --paginate failed or timed out). Abort -- a partial or failed listing must never be audited as if it were the full, or an empty, file set." >&2
  exit 2
fi

if ! FILES_JSON=$(printf '%s' "$RAW_FILES" | _lib_jq -R -s 'split("\n") | map(select(length > 0))' 2>/dev/null); then
  echo "review-pr-diff.sh: could not encode PR $OWNER_REPO#$PR_NUMBER's file list as JSON. Abort." >&2
  exit 2
fi

# Same TOCTOU guard as review-pr-checkout.sh: a force-push landing between
# the initial headRefOid fetch and the file-list fetch would leave the
# audit running against a file list gh already considers stale.
HEAD_REF_OID_RECHECK=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || HEAD_REF_OID_RECHECK=""
if [[ -z "$HEAD_REF_OID_RECHECK" ]]; then
  echo "review-pr-diff.sh: could not re-fetch PR $OWNER_REPO#$PR_NUMBER's headRefOid to confirm the file list above is still current. Abort." >&2
  exit 2
fi
if [[ "$HEAD_REF_OID_RECHECK" != "$HEAD_REF_OID" ]]; then
  echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER's headRefOid changed from $HEAD_REF_OID to $HEAD_REF_OID_RECHECK while its file list was being fetched -- a force-push race. Abort." >&2
  exit 2
fi

# The audit's own exit code mirrors its "stop" verdict (1 = stop, 0 =
# clean, 2 = malformed stdin) -- but unlike review-pr-checkout.sh, a stop
# verdict here is reported, not fatal: no checkout means no subject for the
# audit's own protection, while a PR touching .claude/hooks/** or
# .mcp.json is exactly what an inbound reviewer must flag in the review
# itself. Same predicate, different disposition.
if AUDIT_OUTPUT=$(printf '%s' "$FILES_JSON" | python3 "$AUDIT_SCRIPT" 2>/dev/null); then
  AUDIT_EXIT=0
else
  AUDIT_EXIT=$?
fi
if [[ "$AUDIT_EXIT" -eq 2 ]]; then
  echo "review-pr-diff.sh: audit-execution-surface.py rejected its own self-fetched file list as malformed input: $AUDIT_OUTPUT" >&2
  exit 2
fi
if [[ "$AUDIT_EXIT" -ne 0 ]]; then
  MATCHES=$(printf '%s' "$AUDIT_OUTPUT" | _lib_jq -r '.matches[] | "\(.path): \(.reason)"' 2>/dev/null) || MATCHES="$AUDIT_OUTPUT"
  echo "review-pr-diff.sh: AUDIT_FINDING -- passive-execution audit matched paths in PR $OWNER_REPO#$PR_NUMBER's file list. Treat this as a mandatory blocking finding in the synthesized review, not a stop -- no checkout means nothing landed on disk. Matched paths:" >&2
  printf '%s\n' "$MATCHES" >&2
fi

GH_PR_DIFF_TIMEOUT_SECONDS=30
if ! DIFF_TEXT=$(_lib_gh "$GH_PR_DIFF_TIMEOUT_SECONDS" pr diff "$PR_NUMBER" -R "$OWNER_REPO" 2>/dev/null); then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's diff (gh pr diff failed or timed out). Abort." >&2
  exit 2
fi
# Truncation heuristic, not a vendor-documented limit -- gh pr diff's own
# truncation behavior on a very large PR is unverified. Each
# changed file produces exactly one `diff --git a/... b/...` header line in
# unified diff output (added, deleted, renamed, or binary), so counting
# those headers against the paginated file count is a structural
# completeness check, not an arbitrary byte cap.
DIFF_HEADER_COUNT=$(printf '%s\n' "$DIFF_TEXT" | grep -c '^diff --git ' || true)
FILES_COUNT=$(printf '%s' "$FILES_JSON" | _lib_jq 'length' 2>/dev/null) || FILES_COUNT=""
if [[ -n "$FILES_COUNT" && "$DIFF_HEADER_COUNT" != "$FILES_COUNT" ]]; then
  echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER's diff carries $DIFF_HEADER_COUNT file header(s) but the paginated file list has $FILES_COUNT entries -- the diff response may be truncated. Report this discrepancy in the review; do not treat the diff as complete." >&2
fi

ACTIVE_DIR="$CONFIG_DIR/.review-pr-active.d"
mkdir -p -- "$ACTIVE_DIR"
DIFF_FILE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" diff)
if ! printf '%s\n' "$DIFF_TEXT" | _lib_write_no_follow "$DIFF_FILE"; then
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
