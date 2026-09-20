#!/bin/bash
# _respond-pr-lib.sh — shared pure checks for respond-pr-safe-patch.sh.
#
# Sourced by respond-pr-safe-patch.sh; not executable on its own.
#
# Provides:
#   RESPOND_PR_OWNERSHIP_MARKER          — prefix marking a Claude-authored comment
#   respond_pr_valid_repo_slug           — <owner>/<repo> shape check
#   respond_pr_valid_comment_id          — numeric comment id check
#   respond_pr_body_is_blank             — empty / whitespace-only body check
#   respond_pr_body_is_claude_marked     — body starts with the ownership marker
#
# Every function takes its input as $1, returns 0 (true) or 1 (false), and
# performs no gh call and no filesystem access.

RESPOND_PR_OWNERSHIP_MARKER='**[Claude Code]**'

# Succeeds only for exactly two `/`-separated segments drawn from [A-Za-z0-9._-].
# A slug must be exactly owner/repo so it cannot add path components to the
# `repos/<slug>/pulls/comments/<id>` gh api URL.
# A segment made only of dots is not rejected.
respond_pr_valid_repo_slug() {
  [[ "$1" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]
}

respond_pr_valid_comment_id() {
  [[ "$1" =~ ^[0-9]+$ ]]
}

respond_pr_body_is_blank() {
  [[ -z "${1//[[:space:]]/}" ]]
}

respond_pr_body_is_claude_marked() {
  # Quoted expansion, not bare -- unquoted, [Claude Code] is a glob
  # bracket-expression (matches any one char), not the literal string.
  # `:?` aborts on an empty marker, since an empty prefix would match every body.
  [[ "$1" == "${RESPOND_PR_OWNERSHIP_MARKER:?}"* ]]
}
