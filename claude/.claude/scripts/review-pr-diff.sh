#!/usr/bin/env bash
# No-checkout review path for /review-pr's checkout-or-diff-only branch --
# review-pr-checkout.sh's trust block otherwise makes a cross-repo or
# first-time-contributor PR permanently unreviewable, since that class is
# exactly the common case on a public repo this skill exists to review.
# Mirrors review-pr-checkout.sh's self-derivation discipline (own
# PR-identity parse, own origin-identity check, own paginated file list,
# own double headRefOid fetch bracketing the pagination) minus everything
# that only matters once code lands on disk: no worktree.
set -euo pipefail

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-pr-diff.sh <owner>/<repo>#<number>

Self-derives the PR's own file list and headRefOid, the same discipline
review-pr-checkout.sh uses, but never checks the PR out. In order:
1. Checks that <owner>/<repo> matches this worktree's own origin remote,
   aborting before any gh call on a mismatch.
2. Fetches the PR's current headRefOid and its REST `changed_files` count.
   Stops naming the limit when the PR changes more than 300 files, since
   GitHub's diff endpoint cannot serve one.
3. Fetches the full, paginated file list (one JSON string per file name, so a
   name holding a newline stays one name) and aborts when its length differs
   from `changed_files`.
4. Re-fetches headRefOid to catch a force-push landing while the file list was
   being paginated, and aborts on drift.
5. Pipes the file list to audit-execution-surface.py. A stop verdict is
   reported on stderr as a mandatory pre-seeded finding rather than a stop:
   nothing is landing on disk here for that predicate to protect, and a PR
   touching .claude/hooks/** or .mcp.json is precisely what an inbound reviewer
   must flag. An audit that fails to return a verdict (python3 missing, a
   signal death, an uncaught exception, an exit 0 without a clean verdict on
   stdout) aborts with exit 2 and its stderr shown, and is never reported as a
   finding.
6. Fetches `gh pr diff`, writes it to
   $CONFIG_DIR/.review-pr-active.d/$SESSION_ID.diff, and rewrites this
   session's provenance file with mode "diff-only".

Prints the diff file's path on stdout as the sole output of a successful run.
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
if _lib_case_insensitive_ne "$ORIGIN_OWNER_REPO" "$OWNER_REPO"; then
  echo "review-pr-diff.sh: PR identity '$PR_IDENTITY' names repo '$OWNER_REPO', which does not match this worktree's own origin remote ('$ORIGIN_OWNER_REPO'). Abort before any fetch." >&2
  exit 2
fi

AUDIT_SCRIPT="$CONFIG_DIR/skills/review-pr/audit-execution-surface.py"
if [[ ! -f "$AUDIT_SCRIPT" ]]; then
  echo "review-pr-diff.sh: audit script not found at $AUDIT_SCRIPT -- the review-pr skill is not installed under this config dir. Abort before any fetch." >&2
  exit 2
fi

GH_PR_VIEW_TIMEOUT_SECONDS=10
HEAD_REF_OID=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || HEAD_REF_OID=""
if [[ -z "$HEAD_REF_OID" ]]; then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's current headRefOid. Abort." >&2
  exit 2
fi

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

# The changed-file ceiling of GitHub's diff endpoint: `gh pr diff` cannot
# serve a PR past it, so a named stop here beats a bare gh failure below.
# [unverified] 300 is the limit in GitHub's 406 error text as third parties report it; no REST reference page states it.
DIFF_MAX_CHANGED_FILES=300
if [[ "$CHANGED_FILES_COUNT" -gt "$DIFF_MAX_CHANGED_FILES" ]]; then
  echo "review-pr-diff.sh: PR $OWNER_REPO#$PR_NUMBER changes $CHANGED_FILES_COUNT files, over the $DIFF_MAX_CHANGED_FILES-file limit GitHub's diff endpoint serves -- no diff can be fetched. Abort; this PR needs a route other than gh pr diff." >&2
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
# itself. Same predicate, different disposition. A status alone is not proof
# of a verdict (an uncaught exception also exits 1, an empty script exits 0), so
# review_pr_audit_verdict reads the status together with stdout, and anything
# but a verdict aborts instead of reporting a finding. The audit's stderr is
# not redirected, so a failed run shows its own error. -I keeps PYTHON*
# variables and the user site directory out of the gate; the audit imports only
# json and sys.
if AUDIT_OUTPUT=$(printf '%s' "$FILES_JSON" | python3 -I "$AUDIT_SCRIPT"); then
  AUDIT_EXIT=0
else
  AUDIT_EXIT=$?
fi
case "$(review_pr_audit_verdict "$AUDIT_EXIT" "$AUDIT_OUTPUT")" in
  clean) ;;
  stop)
    MATCHES=$(printf '%s' "$AUDIT_OUTPUT" | _lib_jq -r '.matches[] | "\(.path): \(.reason)"' 2>/dev/null) || MATCHES="$AUDIT_OUTPUT"
    echo "review-pr-diff.sh: AUDIT_FINDING -- passive-execution audit matched paths in PR $OWNER_REPO#$PR_NUMBER's file list. Treat this as a mandatory blocking finding in the synthesized review, not a stop -- no checkout means nothing landed on disk. Matched paths:" >&2
    printf '%s\n' "$MATCHES" >&2
    ;;
  *)
    echo "review-pr-diff.sh: audit-execution-surface.py returned no verdict (exit $AUDIT_EXIT, first line of stdout: $(review_pr_audit_stdout_excerpt "$AUDIT_OUTPUT")), so the PR's file list was not audited -- a tooling failure, not a finding (any stderr the audit wrote is above). Abort." >&2
    exit 2
    ;;
esac

GH_PR_DIFF_TIMEOUT_SECONDS=30
if ! DIFF_TEXT=$(_lib_gh "$GH_PR_DIFF_TIMEOUT_SECONDS" pr diff "$PR_NUMBER" -R "$OWNER_REPO" 2>/dev/null); then
  echo "review-pr-diff.sh: could not fetch PR $OWNER_REPO#$PR_NUMBER's diff (gh pr diff failed or timed out). Abort." >&2
  exit 2
fi

ACTIVE_DIR="$CONFIG_DIR/.review-pr-active.d"
if ! mkdir -p -- "$ACTIVE_DIR"; then
  echo "review-pr-diff.sh: could not create the active directory $ACTIVE_DIR -- cannot record this diff. Abort." >&2
  exit 2
fi
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
