#!/usr/bin/env bash
# Audit-then-checkout for /review-pr Step 2. This script re-derives the PR's
# own repo identity, file list, and headRefOid itself from `gh`/git, never
# trusting Step 1's own read or a value passed in as an argument -- the same
# self-verifying pattern review-pr-post.sh already uses for the post step. A
# PreToolUse hook can only confirm that *some* audit ran, not that its input
# went untampered: the same prompt injection this audit guards against could
# just as easily instruct the agent to "audit an empty list" before such a
# hook's own check, or to invoke this very script against a different,
# attacker-controlled repo than the one actually being checked out. So
# nothing upstream -- including a compromised Step 1 read, or the $1
# argument's own repo identity -- can hand this script a doctored file
# list or a mismatched repo.
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
2. Fetches this PR's own author_association and cross-repo status from
   `gh api repos/{owner}/{repo}/pulls/{number}`, on every invocation. Refuses
   an author whose association is not one of MEMBER, OWNER, COLLABORATOR, or
   CONTRIBUTOR, and a cross-repository PR (including a deleted-fork PR, whose
   head.repo reads null), before any further fetch. Both refusals name
   review-pr-diff.sh as the path to use instead.
3. Fetches the PR's own full, paginated file list (one JSON string per file
   name, so a name holding a newline decodes intact) and its current
   headRefOid directly from `gh`. Aborts on a listing whose length differs
   from the PR's own `changed_files` count.
4. Re-fetches headRefOid to catch a force-push landing while the file list was
   being paginated.
5. Pipes the file list to audit-execution-surface.py. A stop verdict exits 3
   before any fetch of the PR's ref, naming the matched paths and reasons on
   stderr. An audit that fails to return a verdict (python3 missing, a signal
   death, an uncaught exception, an exit 0 without a clean verdict on stdout)
   exits 2 with its stderr shown.
6. On a clean audit, fetches refs/pull/<N>/head and asserts the fetched SHA
   equals the headRefOid fetched earlier in the same run. A mismatch means a
   force-push landed between audit and checkout, and aborts with no worktree
   left behind.
7. Creates a new worktree under the main tree's .claude/worktrees/, named for
   this session and the PR number plus a random suffix. A second run against
   the same PR gets its own worktree; review-pr-finish.sh removes every
   worktree of the session.
8. Rewrites this session's provenance file with mode "checkout" (PR identity,
   the verified headRefOid, this session's Claude PID, the mode), never
   trusting review-pr-acquire.sh's mode "acquired" write.

Prints the worktree's absolute path on stdout as the sole output of a
successful run.

Exit status: 0 on success, 3 when checkout is positively refused and
review-pr-diff.sh is the path to use instead (the PR's trust class, a
cross-repository head, or an audit stop verdict), 2 on every other refusal or
failure, including a check that could not be completed.
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
  echo "review-pr-checkout.sh: PR identity '$PR_IDENTITY' names repo '$OWNER_REPO', which does not match this worktree's own origin remote ('$ORIGIN_OWNER_REPO'). Abort before any fetch -- see this script's header comment for the cross-repo substitution this check exists to close." >&2
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
# authorAssociation is not a `gh pr view --json` field (REFERENCES.md), so
# this REST call is the only way to get it; it also carries head.repo/
# base.repo, deriving cross-repo status independently of step 1's own
# isCrossRepository field rather than trusting that upstream read.
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
HEAD_REF_OID=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || HEAD_REF_OID=""
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

# Re-fetch headRefOid after the file list to catch a force-push during
# pagination, before the audit runs against a possibly-stale list.
HEAD_REF_OID_RECHECK=$(_lib_gh "$GH_PR_VIEW_TIMEOUT_SECONDS" pr view "$PR_NUMBER" -R "$OWNER_REPO" --json headRefOid --jq .headRefOid 2>/dev/null) || HEAD_REF_OID_RECHECK=""
if [[ -z "$HEAD_REF_OID_RECHECK" ]]; then
  echo "review-pr-checkout.sh: could not re-fetch PR $OWNER_REPO#$PR_NUMBER's headRefOid to confirm the file list above is still current. Abort before any fetch of the PR's ref." >&2
  exit 2
fi
if [[ "$HEAD_REF_OID_RECHECK" != "$HEAD_REF_OID" ]]; then
  echo "review-pr-checkout.sh: PR $OWNER_REPO#$PR_NUMBER's headRefOid changed from $HEAD_REF_OID to $HEAD_REF_OID_RECHECK while its file list was being fetched -- a force-push race between the two self-fetches. Abort before any fetch of the PR's ref." >&2
  exit 2
fi

# The audit's own exit code mirrors its "stop" verdict (1 = stop, 0 = clean,
# 2 = malformed stdin), so a status alone is not proof of a verdict: an
# uncaught exception also exits 1, and an empty script exits 0.
# review_pr_audit_verdict reads the status together with stdout, and anything
# but a verdict exits 2. -I keeps PYTHON* variables and the user site directory
# out of the gate; the audit imports only json and sys. The status is
# captured through the if/else exemption from `set -e`
# (shell-script-conventions.md) rather than a bare pipeline, so the stop path
# can still print the audit's own named matches before exiting. The audit's
# stderr is not redirected, so a failed run shows its own error.
if AUDIT_OUTPUT=$(printf '%s' "$FILES_JSON" | python3 -I "$AUDIT_SCRIPT"); then
  AUDIT_EXIT=0
else
  AUDIT_EXIT=$?
fi

case "$(review_pr_audit_verdict "$AUDIT_EXIT" "$AUDIT_OUTPUT")" in
  clean) ;;
  stop)
    MATCHES=$(printf '%s' "$AUDIT_OUTPUT" | _lib_jq -r '.matches[] | "\(.path): \(.reason)"' 2>/dev/null) || MATCHES="$AUDIT_OUTPUT"
    echo "review-pr-checkout.sh: passive-execution audit stopped PR $OWNER_REPO#$PR_NUMBER before checkout -- no refs/pull/$PR_NUMBER/head fetch was made. Use ~/.claude/scripts/review-pr-diff.sh instead. Matched paths:" >&2
    printf '%s\n' "$MATCHES" >&2
    exit "$EXIT_CHECKOUT_REFUSED"
    ;;
  *)
    echo "review-pr-checkout.sh: audit-execution-surface.py returned no verdict (exit $AUDIT_EXIT, first line of stdout: $(review_pr_audit_stdout_excerpt "$AUDIT_OUTPUT")), so the PR's file list was not audited -- a tooling failure, not a stop (any stderr the audit wrote is above). Abort before any fetch of the PR's ref." >&2
    exit 2
    ;;
esac

# Local ref namespace scoped to this script, distinct from any branch name a
# contributor might already have locally, so this fetch can never collide
# with or overwrite an unrelated ref.
LOCAL_REF="refs/review-pr/pr-$PR_NUMBER"
# 30s, matching the files-listing budget above: a network fetch, not a local
# read.
GH_FETCH_TIMEOUT_SECONDS=30
if _lib_capped_for "$GH_FETCH_TIMEOUT_SECONDS" git -C "$REPO_ROOT" -c core.hooksPath=/dev/null fetch origin "refs/pull/$PR_NUMBER/head:$LOCAL_REF" --force >/dev/null 2>&1; then
  FETCH_STATUS=0
else
  FETCH_STATUS=$?
fi
if [[ "$FETCH_STATUS" -ne 0 ]]; then
  if _lib_status_consistent_with_cap_kill "$FETCH_STATUS"; then
    echo "review-pr-checkout.sh: could not fetch refs/pull/$PR_NUMBER/head for PR $OWNER_REPO#$PR_NUMBER from origin: git fetch exited $FETCH_STATUS, consistent with the ${GH_FETCH_TIMEOUT_SECONDS}s cap firing. Abort." >&2
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
# review-pr-finish.sh discovers it from the same root. Every other use of
# $REPO_ROOT in this script (fetch, rev-parse, remote get-url) is unaffected:
# those correctly hit the shared object/ref/config store from either tree.
MAIN_REPO_ROOT=$(_lib_main_repo_root) || {
  echo "review-pr-checkout.sh: could not resolve this repository's main tree root. Abort with no worktree created." >&2
  exit 2
}

SESSION_AND_PID=$(_lib_resolve_claude_pid) || {
  echo "review-pr-checkout.sh: could not resolve this session's id (capture-session-id.sh SessionStart hook did not run) -- cannot name the worktree or record provenance. Abort before creating a worktree." >&2
  exit 2
}
SESSION_ID="${SESSION_AND_PID%% *}"
CLAUDE_PID="${SESSION_AND_PID##* }"
if ! _lib_valid_session_id_component "$SESSION_ID"; then
  echo "review-pr-checkout.sh: resolved session id '$SESSION_ID' is not a valid path component -- cannot name the worktree or record provenance. Abort before creating a worktree." >&2
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
if ! mkdir -p -- "$(dirname "$PROVENANCE")"; then
  echo "review-pr-checkout.sh: could not create the provenance directory $(dirname "$PROVENANCE") -- cannot record this checkout. The worktree at $WORKTREE_DIR was created; run ~/.claude/scripts/review-pr-finish.sh to clean it up. Abort." >&2
  exit 2
fi
if ! _lib_write_review_pr_provenance "$PROVENANCE" \
  "pr_identity=$PR_IDENTITY" "head_ref_oid=$HEAD_REF_OID" "pid=$CLAUDE_PID" "mode=checkout"; then
  echo "review-pr-checkout.sh: could not write provenance file $PROVENANCE -- cannot record this checkout. The worktree at $WORKTREE_DIR was created; run ~/.claude/scripts/review-pr-finish.sh to clean it up. Abort." >&2
  exit 2
fi

printf '%s\n' "$WORKTREE_DIR"
