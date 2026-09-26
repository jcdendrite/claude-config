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
respond_pr_valid_repo_slug() (
  LC_ALL=C  # see respond_pr_valid_comment_id below for why
  [[ "$1" =~ ^[A-Za-z0-9._-]+/[A-Za-z0-9._-]+$ ]]
)

# LC_ALL=C pins bash's bracket-expression matching to raw ASCII bytes. A UTF-8
# locale's glibc collation equivalence classes otherwise widen [0-9] to match
# non-ASCII lookalikes such as Arabic-Indic digits, so the same input would
# validate differently depending on the caller's ambient locale.
# The body is a subshell rather than `local LC_ALL=C`, matching
# _lib_passes_path_char_allowlist in claude/.claude/hooks/_lib.sh: that
# file documents local's restore-on-return as unverified on bash 4.x.
respond_pr_valid_comment_id() (
  LC_ALL=C
  [[ "$1" =~ ^[0-9]+$ ]]
)

# [[:space:]] shares the bracket-expression locale-widening in the two
# predicates above (a UTF-8 locale admits NBSP/ideographic space here too),
# but it's intentionally left unpinned: see
# test_ascii_whitespace_only_body_is_blank_under_c_locale in
# claude/.claude/scripts/tests/test_respond_pr_lib.py.
respond_pr_body_is_blank() {
  [[ -z "${1//[[:space:]]/}" ]]
}

respond_pr_body_is_claude_marked() {
  # Quoted expansion, not bare -- unquoted, [Claude Code] is a glob
  # bracket-expression (matches any one char), not the literal string.
  # `:?` aborts on an empty marker, since an empty prefix would match every body.
  [[ "$1" == "${RESPOND_PR_OWNERSHIP_MARKER:?}"* ]]
}
