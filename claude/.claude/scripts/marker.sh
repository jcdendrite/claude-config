#!/bin/bash
# Write or remove review markers for Claude Code workflow skills.
# Called from SKILL.md HOOK_TEST_FIXTURE fenced blocks.
# Usage: marker.sh <write|activate|deactivate|clear-stale> [<skill>|--dry-run]
# _marker_lib_repo_hash is defined in the sourced library so the hash recipe
# stays in sync with the read side (require-*.sh hooks) automatically.
# BASH_SOURCE[0], not $0: a test that sources this file (rather than
# executing it) to call a function like _hash_staged_diff directly leaves
# $0 naming the outer interpreter, not this file -- BASH_SOURCE[0] always
# names this file regardless of how it was reached.
# shellcheck source=../hooks/_lib.sh
. "$(dirname "${BASH_SOURCE[0]}")/../hooks/_lib.sh"

set -u

# Sibling script, same directory as marker.sh itself -- used by the `status`
# arm below via _lib_cumulative_diff_hash. The `write cumulative-review` arm
# reads a recorded subject instead of calling this script directly.
PR_DIFF_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/pr-diff-against-base.sh"

# Sibling script, invoked by the `write review-pr` arm below to mechanically
# backstop SKILL.md's synthesize-and-record step's own prose scrubbing
# instruction before the completion marker is written -- see that script's
# own header for what it scans for.
REVIEW_PR_SCAN_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/review-pr-scan-findings-body.sh"

# Sibling script, invoked by the `write review-pr` arm below to mechanically
# backstop SKILL.md's synthesize-and-record step's own attribution-prefix/
# trailer/disclosure instruction before the completion marker is written --
# see that script's own header for what it checks.
REVIEW_PR_ATTRIBUTION_SCRIPT="$(dirname "${BASH_SOURCE[0]}")/review-pr-check-attribution.sh"

# The pathspecs are load-bearing: scope the hash to SKILL.md diffs (stowed,
# plugin, plugin-root-equals-repo-root, and this repo's own project-layer
# locations) plus plan-review/ROUTING.md, matching what require-skill-review.sh
# checks at commit time.
SKILL_REVIEW_PATHSPECS=('claude-skills/skills/**/SKILL.md' 'plugins/*/skills/**/SKILL.md' 'skills/**/SKILL.md' '.claude/skills/**/SKILL.md' 'claude-skills/skills/plan-review/ROUTING.md')

# Single registry for every `write <skill>` target -- usage()'s own two
# enum lines (the "status" description and the "Valid combinations" write
# line) and the `write)` case's own *) rejection message all read this
# array rather than carrying their own, independently-maintained copy of
# the same seven names.
WRITE_SKILLS=(code-review skill-review plan-review ready-for-review cumulative-review review-pr verification)

# Parallel indexed arrays (bash 3.2 has no associative arrays), replacing
# six near-identical `activate`/`deactivate`/`status` case/call sites that
# each repeated a skill's own active-bypass directory name. review-pr
# carries no activate/deactivate arm: its Step 1 reads need no active-bypass
# marker of their own (see require-respond-pr.sh's own header for why), so
# it has no directory entry here.
ACTIVE_BYPASS_SKILLS=(plan-review ready-for-review respond-pr memory-skill handoff)
ACTIVE_BYPASS_DIRS=(.plan-review-active.d .ready-for-review-active.d .respond-pr-active.d .memory-skill-active.d .handoff-active.d)

# _join_words SEPARATOR WORD...
# Prints WORDs joined by SEPARATOR (which may be multiple characters, e.g.
# " | " -- IFS-based joining only supports a single-character separator).
_join_words() {
  local sep="$1"; shift
  local first=1 word
  for word in "$@"; do
    if [ "$first" -eq 1 ]; then
      printf '%s' "$word"
      first=0
    else
      printf '%s%s' "$sep" "$word"
    fi
  done
}

# _active_bypass_dir_for SKILL
# Prints SKILL's active-bypass directory name (e.g. ".plan-review-active.d")
# and returns 0, or returns 1 with no output when SKILL is not in
# ACTIVE_BYPASS_SKILLS.
_active_bypass_dir_for() {
  local skill="$1" i
  for i in "${!ACTIVE_BYPASS_SKILLS[@]}"; do
    if [ "${ACTIVE_BYPASS_SKILLS[$i]}" = "$skill" ]; then
      printf '%s' "${ACTIVE_BYPASS_DIRS[$i]}"
      return 0
    fi
  done
  return 1
}

# _active_bypass_skill_list
# Prints ACTIVE_BYPASS_SKILLS as a comma-and-space-separated list, for the
# `activate`/`deactivate` *) arms' rejection messages.
_active_bypass_skill_list() {
  _join_words ', ' "${ACTIVE_BYPASS_SKILLS[@]}"
}

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/marker.sh <subcommand> [<skill>|--dry-run]

Subcommands:
  write      Write a completion marker for the given skill
  activate   Write an active-bypass marker for the given skill
  deactivate Remove the active-bypass marker for the given skill
  clear-stale [--dry-run]
             Evict active-bypass markers whose originating session is no
             longer alive, or whose mtime has aged past the 60-minute idle
             window. --dry-run reports without removing.
  resolve-session-id
             Print this session's canonically-resolved session id. Takes no
             skill argument.
EOF
  printf '  status     Report every completion marker (%s) for\n' "$(_join_words ', ' "${WRITE_SKILLS[@]}")" >&2
  printf '             this repo and every active-bypass marker (%s)\n' "$(_active_bypass_skill_list)" >&2
  cat >&2 <<'EOF'
             for this session, each as live, historical, or absent. Takes no
             skill argument. Evicts a stale (dead-PID) active-bypass marker
             for this session as a side effect of classifying it.
  check      Report whether a completion marker already matches the
             current state, without writing anything. Exit 0 and print
             "match" if it does, exit 1 and print "no-match" if it
             doesn't.

Valid (subcommand, skill) combinations:
EOF
  printf '  write       %s\n' "$(_join_words ' | ' "${WRITE_SKILLS[@]}")" >&2
  printf '  activate    %s\n' "$(_join_words ' | ' "${ACTIVE_BYPASS_SKILLS[@]}")" >&2
  printf '  deactivate  %s\n' "$(_join_words ' | ' "${ACTIVE_BYPASS_SKILLS[@]}")" >&2
  printf '  check       code-review | verification\n' >&2
}

_walk_session() {
  # Delegates to _lib.sh's _lib_resolve_claude_pid, which is this same
  # ancestor walk (moved there so hooks can call it directly without
  # sourcing this file). Kept as a thin wrapper, not inlined at call sites,
  # so this file's own error message stays specific to marker.sh's context.
  local out
  out=$(_lib_resolve_claude_pid) || {
    printf 'marker.sh: SESSION_ID empty — capture-session-id.sh SessionStart hook did not run. Abort without writing a marker.\n' >&2
    return 2
  }
  printf '%s' "$out"
}

_resolve_session_id() {
  local out sid
  out=$(_walk_session) || return 2
  sid="${out%% *}"
  # Chokepoint for every marker.sh path built from a session id (~15
  # constructions below): reject here once rather than at each call site. An
  # id containing '..' or '/' would escape the marker/active directories once
  # concatenated into a path, turning `rm -f`/`>` against it into an
  # operation against a caller-chosen path.
  if ! _lib_valid_session_id_component "$sid"; then
    printf 'marker.sh: SESSION_ID %s is not a valid path component. Abort without writing a marker.\n' "$sid" >&2
    return 2
  fi
  printf '%s' "$sid"
}

# _resolve_session_and_pid
# Every `activate` arm below needs both SESSION_ID and CLAUDE_PID.
# _resolve_session_id alone only returns the session id.
# Getting the PID too would need a second independent call to
# _walk_session's ps(1)-based ancestor walk.
# This function resolves once and sets both as globals, matching this
# script's existing SESSION_ID/CLAUDE_PID call-site convention.
_resolve_session_and_pid() {
  local out sid
  out=$(_walk_session) || return 2
  sid="${out%% *}"
  # Same chokepoint as _resolve_session_id above.
  if ! _lib_valid_session_id_component "$sid"; then
    printf 'marker.sh: SESSION_ID %s is not a valid path component. Abort without writing a marker.\n' "$sid" >&2
    return 2
  fi
  SESSION_ID="$sid"
  CLAUDE_PID="${out##* }"
}

_refuse_main_tree_under_enforcement() {
  # A marker's path (repo hash) and its contents (staged diff, HEAD, plan set)
  # are all keyed to the tree this script resolves. When the reviewed work
  # lives in a linked worktree but this invocation resolves to the main tree,
  # the marker records a review of a tree nobody reviewed — and the reading
  # hook, resolving the same way, accepts it. There is no payload to import a
  # trusted directory from, so refuse instead of guessing.
  local root="$1" session_git_dir common_git_dir worktree
  _lib_worktree_enforcement_active "$root" || return 0
  # One rev-parse call, one line per query flag, in the order given. For the
  # main working tree both paths are identical; for a linked worktree
  # --absolute-git-dir points at <common>/worktrees/<name>.
  {
    read -r session_git_dir
    read -r common_git_dir
  } < <(_lib_capped git -C "$root" rev-parse --absolute-git-dir --path-format=absolute --git-common-dir 2>/dev/null)
  if [ -z "${session_git_dir:-}" ] || [ -z "${common_git_dir:-}" ]; then
    printf 'marker.sh: could not determine git state for %s. Refusing to write a marker under worktree enforcement.\n' "$root" >&2
    return 2
  fi
  [ "$session_git_dir" != "$common_git_dir" ] && return 0
  # Main tree, but no linked worktree exists: there is no second tree for this
  # marker to be confused with, so it correctly describes the only tree there
  # is. Refusing here would wedge a repo whose staged state was produced
  # outside Claude Code's gated tool calls — a hand-staged edit in a terminal
  # or editor, a CI checkout, or work staged before the repo opted in. The
  # worktree hooks gate tool calls, not ambient git state, so that condition is
  # reachable and legitimate.
  worktree=$(_lib_first_live_linked_worktree "$root") || return 0
  printf 'marker.sh: refusing to write a marker from the main working tree (%s) while worktree enforcement is active and a linked worktree exists (%s).\n' "$root" "$worktree" >&2
  printf 'Markers are keyed to the tree they are written from, so this would record the review against the wrong tree.\n' >&2
  printf 'Re-enter the branch worktree — EnterWorktree{path: "%s"} — and re-run the review skill there.\n' "$worktree" >&2
  return 2
}

_resolve_repo_root() {
  # _lib_repo_root is the raw resolution recipe, shared with
  # pr-diff-against-base.sh --record so both sides resolve a given tree to
  # the identical REPO_ROOT string. require-* hooks compute the hash via
  # printf '%s' "$REPO_ROOT" (no newline), so every side must agree exactly.
  local root
  root=$(_lib_repo_root) || {
    printf 'marker.sh: not inside a git repository\n' >&2
    return 2
  }
  _refuse_main_tree_under_enforcement "$root" || return 2
  printf '%s' "$root"
}

_guard_staged_vs_unstaged() {
  local repo_root="$1"; shift
  local skill="$1"; shift
  if git -C "$repo_root" diff --cached --quiet -- "$@" && ! git -C "$repo_root" diff --quiet -- "$@"; then
    # shellcheck disable=SC2016 # single-quoted for literal display text (the
    # backtick-quoted `git add` is markdown-style formatting, not command
    # substitution); %s below is the only intended expansion.
    printf 'marker.sh: staged diff is empty but unstaged tracked changes exist — run `git add` before /%s.\n' "$skill" >&2
    exit 2
  fi
}

# The well-known SHA-256 digest of empty input, computed once here via a
# subprocess rather than hardcoded -- a raw 64-character hex literal in the
# source trips the redaction hook's long-hex-identifier detector (see
# docs/private-project-redaction.md). Computed once at script load, not per
# call, since the digest is algorithm-fixed and every caller below must not
# pay a second sha256sum call per invocation.
_STAGED_DIFF_EMPTY_DIGEST="$(printf '' | sha256sum | awk '{print $1}')"
readonly _STAGED_DIFF_EMPTY_DIGEST

# _status_glob_has_match DIR PREFIX
# True iff some file in DIR whose name begins with PREFIX exists, regardless
# of content -- used by `status` to tell "historical" (a marker exists for
# this repo but its hash is stale) apart from "absent" (no marker at all).
_status_glob_has_match() {
  local dir="$1" prefix="$2"
  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local -a matched=("$dir/$prefix"*)
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi
  # nullglob leaves the array empty on no match, so the first slot is unset
  # (empty string) precisely when nothing matched.
  [ -n "${matched[0]:-}" ]
}

# _status_report_completion_marker LABEL MARKERS_DIR REPO_HASH_PREFIX CURRENT_VALUE
# Prints "  LABEL: live|historical|absent (...)" for `status`. Returns 0 when
# live so a caller needing an extra live-only check (the reconciliation flag)
# doesn't have to re-derive the state.
_status_report_completion_marker() {
  local label="$1" markers_dir="$2" prefix="$3" current_value="$4"
  if [ -n "$current_value" ] && _lib_marker_value_present "$markers_dir" "$current_value" "$prefix"; then
    printf '  %s: live (hash matches the current state)\n' "$label"
    return 0
  fi
  if _status_glob_has_match "$markers_dir" "$prefix"; then
    printf '  %s: historical (marker present, hash does not match the current state)\n' "$label"
  else
    printf '  %s: absent (no marker for this repo)\n' "$label"
  fi
  return 1
}

# _status_report_review_pr_marker CONFIG_DIR REPO_HASH SESSION_ID CURRENT_HEAD_REF_OID
# Prints "  review-pr: live|historical|absent (...)" for `status`. Reads only this
# session's own marker, through _lib_review_pr_completion_marker_fields (the
# reader review-pr-post.sh uses), never a glob over the repo hash's prefix:
# another session's marker must not read as this session's. Live means the
# marker's second line, the reviewed headRefOid, equals CURRENT_HEAD_REF_OID
# (this session's provenance value). No local HEAD is involved, since no
# reviewed tree need be the current one. A marker the reader rejects as
# malformed reads as absent.
_status_report_review_pr_marker() {
  local config_dir="$1" repo_hash="$2" session_id="$3" current_head_ref_oid="$4"
  local marker_fields marker_head_ref_oid
  if ! marker_fields=$(_lib_review_pr_completion_marker_fields "$config_dir" "$repo_hash" "$session_id"); then
    printf '  review-pr: absent (no readable marker for this session)\n'
    return 0
  fi
  marker_head_ref_oid=$(printf '%s\n' "$marker_fields" | sed -n '2p')
  if [ -n "$current_head_ref_oid" ] && [ "$marker_head_ref_oid" = "$current_head_ref_oid" ]; then
    printf '  review-pr: live (the marker for this session matches the reviewed headRefOid)\n'
  else
    printf '  review-pr: historical (marker for this session present, headRefOid does not match the current review)\n'
  fi
}

# _status_reconciliation_flag LABEL REPO_ROOT [PATHSPEC...]
# Prints a flag line when the working tree holds unstaged changes overlapping
# PATHSPEC (the whole repo when no pathspec is given) -- called only when the
# corresponding marker is live, since "uncommitted changes overlap a
# not-live marker" has nothing to reconcile.
_status_reconciliation_flag() {
  local label="$1" repo_root="$2"; shift 2
  local diff_status
  # Exit code checked exactly, not just nonzero: a capped call that times out
  # also exits nonzero, and must not be misread as "differences found".
  _lib_capped git -C "$repo_root" diff --quiet -- "$@"
  diff_status=$?
  if [ "$diff_status" -eq 1 ]; then
    printf '  %s reconciliation flag: uncommitted changes overlap the diff this marker covers\n' "$label"
  fi
}

# _status_report_active_bypass LABEL DIR_NAME SESSION_ID
# Prints "  LABEL: live|stale|absent (...)" for `status`. Existence is
# captured BEFORE calling _lib_active_bypass_marker_live, which evicts a
# stale marker as a side effect (see that function's own docstring for its
# two eviction triggers) -- otherwise "stale" and "absent" would be
# indistinguishable after the call evicts the file out from under us. This
# is a status-only read: it calls the unrefreshing predicate directly,
# never _lib_active_bypass_marker_live_and_touch, so enumerating status
# here can never itself extend a marker's life.
_status_report_active_bypass() {
  local label="$1" dir_name="$2" session_id="$3"
  local marker_path="$CONFIG_DIR/$dir_name/$session_id"
  local existed_before=0
  [ -f "$marker_path" ] && existed_before=1
  if _lib_active_bypass_marker_live "$dir_name" "$session_id"; then
    printf '  %s: live (bypass marker present for this session)\n' "$label"
  elif [ "$existed_before" -eq 1 ]; then
    printf '  %s: stale (marker evicted: dead PID or idle timeout)\n' "$label"
  else
    printf '  %s: absent (no bypass marker for this session)\n' "$label"
  fi
}

# _marker_mtime_epoch TARGET
# Prints TARGET's mtime as a Unix epoch, GNU stat first then BSD/macOS stat --
# same probe order as ask-new-dependency-disclosure.sh's _file_size (not
# shared via _lib.sh; that hook's comment names this as the canonical form).
# Capped at 5s via _lib_capped_for, the same rationale _lib_staged_diff_hash's
# own _lib_capped wrapping carries: a check call (unlike write) reads state
# it doesn't control, so a stalled stat must not hang the gate it's backing.
_marker_mtime_epoch() {
  local target="$1"
  _lib_capped_for 5 stat -c%Y -- "$target" 2>/dev/null || _lib_capped_for 5 stat -f%m -- "$target" 2>/dev/null
}

# _marker_max_age_or_default RAW DEFAULT
# Prints RAW if it is a well-formed positive integer with no leading zero and
# at most 8 digits, else prints DEFAULT. Malformed (empty, zero, non-digit,
# zero-padded, or 9+ digits) falls back to DEFAULT -- same guard shape as
# nudge-long-turn-subagent.sh's resolve_threshold. Shared by
# _resolve_code_review_check_max_age_seconds and
# _resolve_verification_check_max_age_seconds so the five-branch malformed-
# input case has one copy.
_marker_max_age_or_default() {
  local raw="$1" default="$2"
  case "$raw" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) printf '%s' "$default" ;;
    *) printf '%s' "$raw" ;;
  esac
}

# _resolve_code_review_check_max_age_seconds
# Sets CODE_REVIEW_CHECK_MAX_AGE_SECONDS (global). Default 86400 (24h) is a
# deliberately conservative, ungrounded choice (docs/design-decisions.md
# §62).
_resolve_code_review_check_max_age_seconds() {
  CODE_REVIEW_CHECK_MAX_AGE_SECONDS=$(_marker_max_age_or_default "${CODE_REVIEW_CHECK_MAX_AGE_SECONDS:-}" 86400)
}

# _resolve_verification_check_max_age_seconds
# Sets VERIFICATION_CHECK_MAX_AGE_SECONDS (global). Default 14400 (4h) is a
# deliberately conservative, ungrounded choice
# (docs/design-decisions/ready-for-review-verification-cache.md).
_resolve_verification_check_max_age_seconds() {
  VERIFICATION_CHECK_MAX_AGE_SECONDS=$(_marker_max_age_or_default "${VERIFICATION_CHECK_MAX_AGE_SECONDS:-}" 14400)
}

# _marker_fresh_age MARKERS_DIR EXPECTED_VALUE GLOB_PREFIX MAX_AGE_SECONDS
# Prints the age in seconds of the freshest file in MARKERS_DIR matching
# GLOB_PREFIX that holds EXPECTED_VALUE as a whole line and is younger than
# MAX_AGE_SECONDS, and returns 0. Prints nothing and returns 1 when no such
# file exists. Called only after _lib_marker_value_present has already
# confirmed at least one hash match. That single-grep call stays the cheap
# common-case check for "no hash match at all". This loop only runs once a
# hash match exists, and narrows that match set down to non-stale files.
# The candidates loop below is O(n) in GLOB_PREFIX's accumulated marker
# count. That's bounded today because this repo's own worktree-enforced
# hashing keys REPO_HASH to an ephemeral per-branch path rather than a
# stable long-lived one. A non-worktree-enforced repo would scale
# unboundedly here, since completion markers are never pruned.
_marker_fresh_age() {
  local markers_dir="$1" expected_value="$2" glob_prefix="$3" max_age_seconds="$4"
  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local -a candidates=("$markers_dir/$glob_prefix"*)
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi

  local now candidate mtime age best_age=""
  now=$(date +%s)
  # A failed or non-numeric `now` must not fall through to the arithmetic
  # below. An empty $now makes `age = -mtime`, a large negative number that
  # trivially passes the `-lt max_age_seconds` check. Fail closed here the
  # same way the `check code-review` arm further below treats an
  # empty/failed staged-diff hash as no-match.
  case "$now" in ''|*[!0-9]*) return 1 ;; esac
  for candidate in "${candidates[@]}"; do
    [ -f "$candidate" ] || continue
    # Capped at 5s for the same reason _marker_mtime_epoch is: check reads a
    # marker file it doesn't control.
    _lib_capped_for 5 grep -qFx -e "$expected_value" -- "$candidate" 2>/dev/null || continue
    mtime=$(_marker_mtime_epoch "$candidate")
    [ -n "$mtime" ] || continue
    age=$(( now - mtime ))
    # Clamp a future-mtime marker (clock skew, restore tooling) to 0 rather
    # than reporting a negative age, which would otherwise pass the
    # freshness check unconditionally.
    [ "$age" -lt 0 ] && age=0
    if [ "$age" -lt "$max_age_seconds" ] && { [ -z "$best_age" ] || [ "$age" -lt "$best_age" ]; }; then
      best_age="$age"
    fi
  done
  [ -n "$best_age" ] || return 1
  printf '%s' "$best_age"
}

# Guards the two top-level dispatch `case` statements below (the arg-count
# validation and the main SUBCOMMAND case) so a test can `. marker.sh` to
# call a function directly (e.g. _hash_staged_diff) without also triggering
# the CLI dispatch, which would fail on a missing $1. BASH_SOURCE[0] (the
# file currently executing) differs from $0 (the top-level script or
# interpreter) only when this file is sourced rather than run directly.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  usage
  exit 2
fi

# Fail closed: every marker path below is built from CONFIG_DIR, so an
# unresolvable CLAUDE_CONFIG_DIR (relative value, or empty $HOME with no
# override) must abort the write rather than fall through to a
# root-anchored path.
CONFIG_DIR=$(_lib_config_dir) || {
  # shellcheck disable=SC2016 # single-quoted for literal display text: $HOME
  # and $CLAUDE_CONFIG_DIR name the env vars in the message, not shell expansions.
  printf 'marker.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or $HOME is unset/empty). Abort without writing a marker.\n' >&2
  exit 2
}

# Wraps _lib.sh's shared _lib_review_pr_artifact_path helper at the fixed
# findings-body suffix, so this script's `write review-pr` arm and
# review-pr-post.sh both resolve the same path through one definition --
# SKILL.md Step 7 instructs writing the findings body here and nowhere
# else.
_review_pr_findings_body_fixed_path() {
  _lib_review_pr_artifact_path "$CONFIG_DIR" "$1" body
}

# _read_marker_no_follow SRC_PATH
# Prints SRC_PATH's contents through a single os.open(O_NOFOLLOW) -- refuses
# a symlink at the final path component atomically with the read, mirroring
# _lib.sh's _lib_write_no_follow for the read side of these same predictable
# <active-dir>/<session-id>[.suffix] destinations. Prints nothing and
# returns 1 on a missing file, a symlink, or a permission error -- callers
# treat that the same as an empty/dead value, never a fatal abort.
_read_marker_no_follow() {
  _lib_capped python3 -c '
import os, sys
try:
    fd = os.open(sys.argv[1], os.O_RDONLY | os.O_NOFOLLOW)
except OSError:
    sys.exit(1)
with os.fdopen(fd, "rb") as f:
    sys.stdout.buffer.write(f.read())
' "$1"
}

SUBCOMMAND="$1"
ARG2="${2:-}"

# Validate arg count per subcommand.
case "$SUBCOMMAND" in
  write|activate|deactivate|check)
    if [ -z "$ARG2" ]; then
      usage
      exit 2
    fi
    SKILL="$ARG2"
    ;;
  clear-stale)
    if [ -n "$ARG2" ] && [ "$ARG2" != "--dry-run" ]; then
      usage
      exit 2
    fi
    ;;
  resolve-session-id|status)
    if [ -n "$ARG2" ]; then
      usage
      exit 2
    fi
    ;;
  *)
    printf "marker.sh: unknown subcommand '%s'\n" "$SUBCOMMAND" >&2
    usage
    exit 2
    ;;
esac

case "$SUBCOMMAND" in
  write)
    case "$SKILL" in
      code-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        _guard_staged_vs_unstaged "$REPO_ROOT" code-review
        # Resolved once and threaded into _lib_code_review_marker_value below,
        # so the marker records the same novel-content preimage
        # require-code-review.sh reads -- a mismatch between write-side and
        # read-side base recipes would mean a marker written here can never
        # match on the read side.
        GATE_DIFF_BASE=$(_lib_gate_diff_base "$REPO_ROOT")
        # This call and require-code-review.sh's own _lib_gate_diff_base call
        # each independently hit the ~5s cap, so during an in-progress
        # merge/rebase/cherry-pick/revert a marker can fail to match on read
        # despite unchanged staged content.
        # This never causes a false accept, only a false re-review
        # requirement, so it is an availability gap, not a security one.
        # Compute before redirecting: `>` truncates the marker before the
        # pipeline runs, so a failed hash would destroy a valid marker and
        # silently force a re-review. Same shape in every arm below.
        MARKER_VALUE=$(_lib_code_review_marker_value "$REPO_ROOT" "$GATE_DIFF_BASE")
        if [ -z "$MARKER_VALUE" ]; then
          printf 'marker.sh: could not hash the staged diff. Abort without writing a marker.\n' >&2
          exit 2
        fi
        # $_STAGED_DIFF_EMPTY_DIGEST only equals sha256("") here when
        # GATE_DIFF_BASE is empty (no trusted in-progress state) and the
        # plain staged diff is itself empty -- a mid-operation degenerate-
        # empty-diff case binds to GATE_DIFF_BASE's own identity instead (see
        # _lib_code_review_marker_value), so this check can't misfire on
        # that case.
        if [ "$MARKER_VALUE" = "$_STAGED_DIFF_EMPTY_DIGEST" ]; then
          printf 'marker.sh: staged diff is empty -- nothing to review. Exiting without writing a marker.\n' >&2
          exit 0
        fi
        mkdir -p "$CONFIG_DIR/code-review-markers"
        printf '%s\n' "$MARKER_VALUE" | _lib_write_no_follow "$CONFIG_DIR/code-review-markers/$REPO_HASH.$SESSION_ID" \
          || { printf 'marker.sh: could not write the completion marker (symlink at destination, or permission error). Abort.\n' >&2; exit 2; }
        ;;
      skill-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # The pathspecs are load-bearing: scope the hash to SKILL.md diffs (both stowed
        # and plugin locations) plus plan-review/ROUTING.md, matching what
        # require-skill-review.sh checks at commit time. SKILL_REVIEW_PATHSPECS is
        # the module-level constant defined near the top of this file.
        _guard_staged_vs_unstaged "$REPO_ROOT" skill-review "${SKILL_REVIEW_PATHSPECS[@]}"
        # Resolved once and threaded into _lib_staged_diff_hash below, so the
        # marker records the same novel-content preimage require-skill-review.sh
        # reads -- a mismatch between write-side and read-side base recipes
        # would mean a marker written here can never match on the read side.
        # Mid-revert this excludes (empty stdout), unlike code-review's
        # GATE_DIFF_BASE above -- see
        # docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
        SKILL_REVIEW_BASE=$(_lib_skill_review_diff_base "$REPO_ROOT")
        # Compute before redirecting: `>` truncates the marker before the
        # pipeline runs, so a failed hash would destroy a valid marker and
        # silently force a re-review. Same shape as the code-review arm above.
        MARKER_VALUE=$(_lib_staged_diff_hash "$REPO_ROOT" "$SKILL_REVIEW_BASE" "${SKILL_REVIEW_PATHSPECS[@]}")
        if [ -z "$MARKER_VALUE" ]; then
          # _lib_staged_diff_hash's two-outcome contract collapses a cap kill
          # and an ordinary git failure into the same empty-stdout result, so
          # this message can't distinguish "timed out, retry" from "git
          # failed, investigate" (see that function's docstring in _lib.sh).
          printf 'marker.sh: could not hash the staged SKILL.md diff. Abort without writing a marker.\n' >&2
          exit 2
        fi
        # No empty-base sentinel here; see docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md, "Why no empty-base sentinel exists here".
        if [ "$MARKER_VALUE" = "$_STAGED_DIFF_EMPTY_DIGEST" ]; then
          printf 'marker.sh: staged SKILL.md diff is empty -- nothing to review. Exiting without writing a marker.\n' >&2
          exit 0
        fi
        mkdir -p "$CONFIG_DIR/skill-review-markers"
        printf '%s\n' "$MARKER_VALUE" | _lib_write_no_follow "$CONFIG_DIR/skill-review-markers/$REPO_HASH.$SESSION_ID" \
          || { printf 'marker.sh: could not write the completion marker (symlink at destination, or permission error). Abort.\n' >&2; exit 2; }
        ;;
      plan-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # A plan-mode declaration takes priority over the repo-relative plan
        # set: the /plan-review skill's Step 0 writes the harness-designated
        # plan-mode file's path into this sibling file when the session is in
        # plan mode. Content-addressed like the repo-relative case below —
        # hash the DECLARED TARGET's current content, read fresh here, not
        # the sibling file's own bytes and not any hash computed earlier — so
        # a plan revision mid-review is still caught.
        PLANMODE_SIBLING="$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.planmode-path"
        if PLANMODE_TARGET=$(_lib_capped cat "$PLANMODE_SIBLING" 2>/dev/null); then
          PLAN_HASH=$(_lib_capped sha256sum -- "$PLANMODE_TARGET" 2>/dev/null | awk '{print $1}')
          if [ -z "$PLAN_HASH" ]; then
            # Matches _lib_active_plan_hash's own abort contract below:
            # falling back to the repo-relative hash here would silently
            # write a completion marker that doesn't cover what was reviewed.
            printf 'marker.sh: cannot read plan-mode file %s — cannot compute the plan-review hash. Abort without writing a marker.\n' "$PLANMODE_TARGET" >&2
            exit 2
          fi
        else
          # Content-addressed: the marker holds a hash of the active plan
          # file set (paths + contents), so editing a reviewed plan re-arms
          # require-plan-review.sh on the next gate hit. _lib_active_plan_hash
          # is the single source of truth shared with the read side.
          #
          # Resolved here (not shared with the code-review arm above, which
          # runs in a separate `write` invocation) and threaded into
          # _lib_active_plan_hash, so the marker records the same
          # trusted-base preimage require-plan-review.sh reads.
          #
          # Capture into a variable before redirecting. Writing the
          # function's output straight into the marker path would let `>`
          # truncate an existing valid marker before the function even runs,
          # so a failed attempt would destroy a good marker as a side effect.
          PLAN_GATE_DIFF_BASE=$(_lib_gate_diff_base "$REPO_ROOT")
          # Same residual as the `write code-review` arm above: this call and
          # require-plan-review.sh's own _lib_gate_diff_base call can disagree
          # near the ~5s cap even though the staged content hasn't changed.
          # Fails toward re-review, not toward accepting an unreviewed plan.
          if ! PLAN_HASH=$(_lib_active_plan_hash "$REPO_ROOT" "$PLAN_GATE_DIFF_BASE"); then
            printf 'marker.sh: cannot read active plan file %s — cannot compute the plan-review hash. Abort without writing a marker.\n' "$PLAN_HASH" >&2
            exit 2
          fi
        fi
        mkdir -p "$CONFIG_DIR/plan-review-markers"
        printf '%s\n' "$PLAN_HASH" | _lib_write_no_follow "$CONFIG_DIR/plan-review-markers/$REPO_HASH.$SESSION_ID" \
          || { printf 'marker.sh: could not write the completion marker (symlink at destination, or permission error). Abort.\n' >&2; exit 2; }
        ;;
      ready-for-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # No uncommitted-change guard needed here: this marker's HEAD sha
        # gates push/PR-creation mechanics acting on committed content only,
        # unlike `verification` below (docs/design-decisions/ready-for-review-verification-cache.md).
        MARKER_VALUE=$(git -C "$REPO_ROOT" rev-parse HEAD)
        [ -n "$MARKER_VALUE" ] || { printf 'marker.sh: could not resolve HEAD. Abort without writing a marker.\n' >&2; exit 2; }
        mkdir -p "$CONFIG_DIR/ready-for-review-markers"
        printf '%s\n' "$MARKER_VALUE" | _lib_write_no_follow "$CONFIG_DIR/ready-for-review-markers/$REPO_HASH.$SESSION_ID" \
          || { printf 'marker.sh: could not write the completion marker (symlink at destination, or permission error). Abort.\n' >&2; exit 2; }
        ;;
      cumulative-review)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # No _guard_staged_vs_unstaged call: this marker covers the
        # committed PR-vs-base diff, not the staged diff, so that guard's
        # staged-vs-unstaged question does not apply here.
        #
        # Reads the subject `pr-diff-against-base.sh --record` captured at
        # step 3 entry rather than recomputing (design-decisions.md §50).
        # Emptiness is checked on the canonicalized text below, not the raw
        # file's byte count, to use one definition of "recorded" throughout.
        SUBJECT_FILE="$CONFIG_DIR/cumulative-review-subject-markers/$REPO_HASH.$SESSION_ID"
        if [ ! -e "$SUBJECT_FILE" ]; then
          # shellcheck disable=SC2016 # single-quoted for literal display text (the
          # backtick-quoted command is markdown-style formatting, not command
          # substitution); %s below is the only intended expansion.
          printf 'marker.sh: no recorded cumulative-review subject for %s. Run `~/.claude/scripts/pr-diff-against-base.sh --record` (step 3 already runs this) before writing this marker. Abort without writing a marker.\n' "$REPO_ROOT" >&2
          exit 2
        fi
        # Command substitution strips trailing newlines the same way
        # _lib_cumulative_diff_hash's own diff_output capture does, so the
        # two hashing paths agree byte-for-byte on the same underlying text.
        if ! SUBJECT_TEXT=$(cat "$SUBJECT_FILE" 2>/dev/null); then
          # shellcheck disable=SC2016 # same literal-display-text reasoning as above.
          printf 'marker.sh: could not read the recorded cumulative-review subject at %s (permission denied or similar). Run `~/.claude/scripts/pr-diff-against-base.sh --record` before writing this marker. Abort without writing a marker.\n' "$SUBJECT_FILE" >&2
          exit 2
        fi
        if [ -z "$SUBJECT_TEXT" ]; then
          # shellcheck disable=SC2016 # same literal-display-text reasoning as above.
          printf 'marker.sh: the recorded cumulative-review subject for %s is empty. Run `~/.claude/scripts/pr-diff-against-base.sh --record` (step 3 already runs this) before writing this marker. Abort without writing a marker.\n' "$REPO_ROOT" >&2
          exit 2
        fi
        # Compute before redirecting -- same shape as every other write arm
        # above: `>` truncates the marker before the pipeline runs, so a
        # failed hash would destroy a valid marker and silently force a
        # re-review.
        MARKER_VALUE=$(_lib_hash_diff_text "$SUBJECT_TEXT") || {
          printf 'marker.sh: could not hash the recorded cumulative-review subject. Abort without writing a marker.\n' >&2
          exit 2
        }
        mkdir -p "$CONFIG_DIR/cumulative-review-markers"
        printf '%s\n' "$MARKER_VALUE" | _lib_write_no_follow "$CONFIG_DIR/cumulative-review-markers/$REPO_HASH.$SESSION_ID" \
          || { printf 'marker.sh: could not write the completion marker (symlink at destination, or permission error). Abort.\n' >&2; exit 2; }
        rm -f "$SUBJECT_FILE"
        ;;
      review-pr)
        SESSION_ID=$(_resolve_session_id) || exit 2
        # Provenance file written by review-pr-acquire.sh (mode acquired)
        # and rewritten by review-pr-checkout.sh/review-pr-diff.sh after
        # their own independent re-derivation (mode checkout/diff-only): PR
        # identity, the reviewed headRefOid, the session's Claude PID, and
        # the mode -- never the findings-body text itself, so the findings
        # never land in argv, shell history, or the process table. Read via
        # _lib_review_pr_provenance_field's key=value schema (schema=1
        # header, one KEY=VALUE line per field), not positional line
        # numbers -- a field this arm doesn't know about yet (added by a
        # later phase) is simply never read here rather than shifting every
        # other field's position.
        PROVENANCE=$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)
        PR_IDENTITY=$(_lib_review_pr_provenance_field "$PROVENANCE" pr_identity) || PR_IDENTITY=""
        HEAD_REF_OID=$(_lib_review_pr_provenance_field "$PROVENANCE" head_ref_oid) || HEAD_REF_OID=""
        PROVENANCE_PID=$(_lib_review_pr_provenance_field "$PROVENANCE" pid) || PROVENANCE_PID=""
        MODE=$(_lib_review_pr_provenance_field "$PROVENANCE" mode) || MODE=""
        if [ -z "$PR_IDENTITY" ] || [ -z "$HEAD_REF_OID" ] || [ -z "$MODE" ] || [ -z "$PROVENANCE_PID" ]; then
          printf 'marker.sh: %s is missing, unreadable, or missing PR identity, headRefOid, mode, or PID. Abort without writing a marker.\n' "$PROVENANCE" >&2
          exit 2
        fi
        case "$PROVENANCE_PID" in
          ''|*[!0-9]*)
            printf 'marker.sh: %s has a non-numeric PID field. Abort without writing a marker.\n' "$PROVENANCE" >&2
            exit 2
            ;;
        esac
        # An acquire-only session (mode "acquired") has run neither the
        # checkout audit nor the no-checkout diff path, so it can never
        # write a completion marker -- and any other value is a corrupted
        # or hand-written provenance (any process that can write files can
        # write this skill's own state, an accepted residual risk). Refuse
        # rather than falling through to either known branch by default.
        case "$MODE" in
          checkout | diff-only) ;;
          *)
            printf 'marker.sh: %s has mode %s, which is neither checkout nor diff-only. Abort without writing a marker.\n' "$PROVENANCE" "$MODE" >&2
            exit 2
            ;;
        esac
        # Keyed to the main tree's root, not the current tree's, in both
        # modes: the marker binds a session to a PR review, not to a tree,
        # so it needs no _resolve_repo_root main-tree refusal and can be
        # written from wherever the session stands. The remote headRefOid
        # re-check in review-pr-post.sh is the freshness binding.
        REPO_HASH=$(_lib_review_pr_marker_repo_hash) || {
          printf 'marker.sh: not inside a git repository, or the repo hash could not be computed\n' >&2
          exit 2
        }
        # The findings-body path is derived here, never read from provenance,
        # so no path-equality guard is needed before using it.
        FINDINGS_BODY_PATH=$(_review_pr_findings_body_fixed_path "$SESSION_ID")
        # Mechanical backstop for SKILL.md's synthesize-and-record step's own
        # "start with **[Claude Code]**, end with the trailer" instruction,
        # plus (in diff-only mode) the reduced-coverage disclosure line:
        # same rationale as the secret scan below -- a PreToolUse hook never
        # sees the findings body, since it's composed by the model's own
        # reasoning rather than passed as a tool-call argument. MODE is
        # passed through so the disclosure check cannot be opted out of by a
        # model that never saw the prose stating it. Run before the body is
        # hashed or the completion marker written, so a missing prefix,
        # trailer, or disclosure refuses the whole write rather than
        # getting marker-ized.
        # _lib_capped (5s default): a local `head`/`grep` read over a
        # session-owned text file, the same budget the secret scan below
        # and the BODY_HASH computation further below use for their own
        # reads of this same file.
        if ! ATTRIBUTION_OUTPUT=$(_lib_capped "$REVIEW_PR_ATTRIBUTION_SCRIPT" "$FINDINGS_BODY_PATH" "$MODE" 2>&1); then
          printf '%s\n' "$ATTRIBUTION_OUTPUT" >&2
          printf 'marker.sh: findings-body attribution check failed. Abort without writing a marker.\n' >&2
          exit 2
        fi
        # Mechanical backstop for SKILL.md's synthesize-and-record step's own
        # prose scrubbing instruction: a PreToolUse hook never sees the
        # findings body, since it's composed by the model's own reasoning
        # rather than passed as a tool-call argument. Run before the body is
        # hashed or the completion marker written, so a credential-shaped
        # string still in the body refuses the whole write rather than
        # getting marker-ized.
        # _lib_capped (5s default): a local grep over a session-owned text
        # file, the same budget the BODY_HASH computation just below uses
        # for its own read of this same file.
        if ! SCAN_OUTPUT=$(_lib_capped "$REVIEW_PR_SCAN_SCRIPT" "$FINDINGS_BODY_PATH" 2>&1); then
          printf '%s\n' "$SCAN_OUTPUT" >&2
          printf 'marker.sh: findings-body secret scan failed. Abort without writing a marker.\n' >&2
          exit 2
        fi
        # _lib_sha256_no_follow reads through a single os.open(O_NOFOLLOW) --
        # a separate `[ -L ]` check followed by `sha256sum` is not atomic, so
        # an attacker could swap in a symlink between the two. Compute
        # before redirecting, same reasoning as every arm above: a failed
        # hash must not truncate a valid existing marker.
        BODY_HASH=$(_lib_sha256_no_follow "$FINDINGS_BODY_PATH" 2>/dev/null)
        [ -n "$BODY_HASH" ] || { printf 'marker.sh: could not hash the findings-body file %s (missing, unreadable, or a symlink). Abort without writing a marker.\n' "$FINDINGS_BODY_PATH" >&2; exit 2; }
        mkdir -p "$CONFIG_DIR/review-pr-markers"
        printf '%s\n%s\n%s\n%s\n' "$PR_IDENTITY" "$HEAD_REF_OID" "$BODY_HASH" "$MODE" | _lib_write_no_follow "$CONFIG_DIR/review-pr-markers/$REPO_HASH.$SESSION_ID" \
          || { printf 'marker.sh: could not write the completion marker (symlink at destination, or permission error). Abort.\n' >&2; exit 2; }
        ;;
      verification)
        SESSION_ID=$(_resolve_session_id) || exit 2
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # No _guard_staged_vs_unstaged call: this marker covers the
        # committed tree (HEAD^{tree}), not the staged diff, so that guard's
        # staged-vs-unstaged question does not apply here.
        #
        # Uncommitted content -- staged, unstaged, or untracked -- is
        # invisible to HEAD^{tree}. See
        # docs/design-decisions/ready-for-review-verification-cache.md's
        # residuals for the gitignore blind spot and the delete-before-write
        # TOCTOU this guard still leaves open.
        UNCOMMITTED_STATUS=$(git -C "$REPO_ROOT" status --porcelain) || {
          printf 'marker.sh: could not check %s for uncommitted changes. Abort without writing a marker.\n' "$REPO_ROOT" >&2
          exit 2
        }
        if [ -n "$UNCOMMITTED_STATUS" ]; then
          printf 'marker.sh: uncommitted changes present in %s. Abort without writing a marker.\n' "$REPO_ROOT" >&2
          exit 2
        fi
        #
        # Each marker kind hashes what its own step consumes. `verification`
        # hashes the tree (what step 2 executes); `cumulative-review` hashes
        # the diff (what step 3 reads).
        #
        # Compute before redirecting -- same shape as every other write arm
        # above: `>` truncates the marker before the pipeline runs, so a
        # failed hash would destroy a valid marker and silently force a
        # re-verification.
        MARKER_VALUE=$(_lib_head_tree_hash uncapped "$REPO_ROOT") || {
          printf 'marker.sh: could not resolve HEAD^{tree}. Abort without writing a marker.\n' >&2
          exit 2
        }
        mkdir -p "$CONFIG_DIR/verification-markers"
        printf '%s\n' "$MARKER_VALUE" \
          > "$CONFIG_DIR/verification-markers/$REPO_HASH.$SESSION_ID"
        ;;
      *)
        printf "marker.sh: 'write %s' is not valid. 'write' supports: %s\n" "$SKILL" "$(_join_words ', ' "${WRITE_SKILLS[@]}")" >&2
        exit 2
        ;;
    esac
    ;;
  activate)
    case "$SKILL" in
      plan-review|ready-for-review|respond-pr|memory-skill|handoff)
        ACTIVE_BYPASS_DIR=$(_active_bypass_dir_for "$SKILL")
        _resolve_session_and_pid || exit 2
        mkdir -p "$CONFIG_DIR/$ACTIVE_BYPASS_DIR"
        printf '%s\n' "$CLAUDE_PID" | _lib_write_no_follow "$CONFIG_DIR/$ACTIVE_BYPASS_DIR/$SESSION_ID" \
          || { printf 'marker.sh: could not write the active-bypass marker (symlink at destination, or permission error). Abort.\n' >&2; exit 2; }
        if [ "$SKILL" = "plan-review" ]; then
          # Backfill: a ROUTING.md Read landing just before this activate
          # still counts, via log-routing-read.sh's pending-read record. 5
          # minutes covers a same-turn re-read while staying well inside
          # require-routing-read.sh's 60-minute freshness window, so a stale
          # Read from earlier in the session can't falsely backfill.
          PENDING_READ="$CONFIG_DIR/.plan-review-pending-read.d/$SESSION_ID"
          if [ -f "$PENDING_READ" ] && [ -n "$(find "$PENDING_READ" -mmin -5 2>/dev/null)" ]; then
            mkdir -p "$CONFIG_DIR/.plan-review-routing-read.d"
            touch "$CONFIG_DIR/.plan-review-routing-read.d/$SESSION_ID"
          fi
        fi
        ;;
      *)
        printf "marker.sh: 'activate %s' is not valid. 'activate' supports: %s\n" "$SKILL" "$(_active_bypass_skill_list)" >&2
        exit 2
        ;;
    esac
    ;;
  deactivate)
    case "$SKILL" in
      plan-review|ready-for-review|respond-pr|memory-skill|handoff)
        ACTIVE_BYPASS_DIR=$(_active_bypass_dir_for "$SKILL")
        SESSION_ID=$(_resolve_session_id) || exit 2
        rm -f "$CONFIG_DIR/$ACTIVE_BYPASS_DIR/$SESSION_ID"
        if [ "$SKILL" = "plan-review" ]; then
          rm -f "$CONFIG_DIR/$ACTIVE_BYPASS_DIR/$SESSION_ID.planmode-path"
          rm -f "$CONFIG_DIR/.plan-review-routing-read.d/$SESSION_ID"
          rm -f "$CONFIG_DIR/.plan-review-pending-read.d/$SESSION_ID"
        elif [ "$SKILL" = "ready-for-review" ]; then
          # Best-effort: bounds this session's own recorded-but-unwritten
          # cumulative-review subject, and its step-4 diff-file artifact, to
          # this gate run. Session-suffixed so this only ever removes this
          # session's own artifacts, never another session's still-pending
          # ones. Repo-root resolution is unrelated to the session-scoped
          # removal above, so a failure here must not abort it -- skip the
          # artifact cleanup instead.
          if REPO_ROOT=$(_resolve_repo_root 2>/dev/null); then
            REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
            rm -f "$CONFIG_DIR/cumulative-review-subject-markers/$REPO_HASH.$SESSION_ID"
            rm -f "$CONFIG_DIR/cumulative-review-diff-markers/$REPO_HASH.$SESSION_ID"
          else
            printf 'marker.sh: could not resolve repo root; skipping cumulative-review subject and diff-file cleanup.\n' >&2
          fi
        fi
        ;;
      *)
        printf "marker.sh: 'deactivate %s' is not valid. 'deactivate' supports: %s\n" "$SKILL" "$(_active_bypass_skill_list)" >&2
        exit 2
        ;;
    esac
    ;;
  clear-stale)
    # Sweeps only .*-active.d/ below. cumulative-review-subject-markers/ and
    # cumulative-review-diff-markers/ are unreachable here, so a session
    # killed before `deactivate ready-for-review` leaks that session's
    # artifact indefinitely. Accepted as a low-cost gap:
    #   - each artifact is bounded to one session
    #   - overwritten on the next gate pass
    #   - revisit only if unbounded accumulation shows up in practice
    #
    # .review-pr-active.d keeps that name even though review-pr carries no
    # activate/deactivate arm of its own -- its Step 1 reads need none. This
    # glob is "$CONFIG_DIR"/.*-active.d, so a renamed directory would never
    # be swept.
    #
    # One python3 invocation for the whole sweep, not one per entry: a
    # per-file bash loop would pay a fresh _read_marker_no_follow (its own
    # python3 spawn) per file. Extracted into marker-clear-stale.py per
    # shell-script-conventions.md -- this sweep has its own control flow and
    # data structures, not a single syscall bash can't express.
    DRY_RUN=0
    [ "$ARG2" = "--dry-run" ] && DRY_RUN=1
    CLEAR_STALE_OUTPUT=$(python3 "$(dirname "$0")/marker-clear-stale.py" "$CONFIG_DIR" "$DRY_RUN")
    printf '%s\n' "$CLEAR_STALE_OUTPUT"
    ;;
  resolve-session-id)
    SESSION_ID=$(_resolve_session_id) || exit 2
    printf '%s' "$SESSION_ID"
    ;;
  status)
    SESSION_ID=$(_resolve_session_id) || exit 2
    REPO_ROOT=$(_resolve_repo_root) || exit 2
    REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
    REPO_HASH_PREFIX="$REPO_HASH."

    printf 'Completion markers (this repo):\n'

    # Resolved once for this subcommand arm and reused by code-review and
    # plan-review below -- both thread it in directly. skill-review resolves
    # its own base separately (below) rather than reusing this one: it
    # excludes revert while this GATE_DIFF_BASE does not, so the two answers
    # can differ. See
    # docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md.
    GATE_DIFF_BASE=$(_lib_gate_diff_base "$REPO_ROOT")

    # code-review: hash of the whole-repo staged diff, same recipe as the
    # `write code-review` arm above. _lib_code_review_marker_value is
    # internally capped, so a stalled git diff can't hang the whole status
    # report. A killed process yields an empty value, which
    # _status_report_completion_marker already treats as absent/historical.
    # This excludes the same empty-diff sentinel value that `check`'s own
    # call site below excludes (see its comment for the two-case
    # breakdown), so a leftover marker holding it never reads as live on a
    # clean tree.
    CODE_REVIEW_VALUE=$(_lib_code_review_marker_value "$REPO_ROOT" "$GATE_DIFF_BASE")
    if [ -n "$GATE_DIFF_BASE" ]; then
      CODE_REVIEW_EMPTY_DIFF_HASH=$(_lib_hash_diff_text "$(_lib_code_review_empty_base_sentinel "$GATE_DIFF_BASE")")
    else
      CODE_REVIEW_EMPTY_DIFF_HASH="$_STAGED_DIFF_EMPTY_DIGEST"
    fi
    [ "$CODE_REVIEW_VALUE" = "$CODE_REVIEW_EMPTY_DIFF_HASH" ] && CODE_REVIEW_VALUE=""
    if _status_report_completion_marker code-review "$CONFIG_DIR/code-review-markers" "$REPO_HASH_PREFIX" "$CODE_REVIEW_VALUE"; then
      _status_reconciliation_flag code-review "$REPO_ROOT"
    fi

    # skill-review: same recipe as the `write skill-review` arm above,
    # scoped to the SKILL.md/ROUTING.md pathspecs. No empty-base sentinel; see docs/design-decisions/skill-review-gate-disarms-on-empty-base-relative-diff.md, "Why no empty-base sentinel exists here".
    SKILL_REVIEW_BASE=$(_lib_skill_review_diff_base "$REPO_ROOT")
    SKILL_REVIEW_VALUE=$(_lib_staged_diff_hash "$REPO_ROOT" "$SKILL_REVIEW_BASE" "${SKILL_REVIEW_PATHSPECS[@]}")
    [ "$SKILL_REVIEW_VALUE" = "$_STAGED_DIFF_EMPTY_DIGEST" ] && SKILL_REVIEW_VALUE=""
    if _status_report_completion_marker skill-review "$CONFIG_DIR/skill-review-markers" "$REPO_HASH_PREFIX" "$SKILL_REVIEW_VALUE"; then
      _status_reconciliation_flag skill-review "$REPO_ROOT" "${SKILL_REVIEW_PATHSPECS[@]}"
    fi

    # plan-review: same recipe as the `write plan-review` arm above (the
    # plan-mode sibling takes priority over _lib_active_plan_hash). A hash
    # that can't be computed (unreadable plan-mode target) is treated as
    # empty here rather than aborting -- `status` is a report, not a write,
    # and the other three markers still deserve their own report.
    PLANMODE_SIBLING="$CONFIG_DIR/.plan-review-active.d/$SESSION_ID.planmode-path"
    if PLANMODE_TARGET=$(_lib_capped cat "$PLANMODE_SIBLING" 2>/dev/null); then
      PLAN_REVIEW_VALUE=$(_lib_capped sha256sum -- "$PLANMODE_TARGET" 2>/dev/null | awk '{print $1}')
    else
      PLAN_REVIEW_VALUE=$(_lib_active_plan_hash "$REPO_ROOT" "$GATE_DIFF_BASE") || PLAN_REVIEW_VALUE=""
    fi
    _status_report_completion_marker plan-review "$CONFIG_DIR/plan-review-markers" "$REPO_HASH_PREFIX" "$PLAN_REVIEW_VALUE"

    # ready-for-review: same recipe as the `write ready-for-review` arm
    # above. Unlike that arm, stderr is suppressed and an empty result is not
    # fatal -- a zero-commit repo has no HEAD to hash, which `status` must
    # report as absent rather than error on.
    READY_FOR_REVIEW_VALUE=$(_lib_capped git -C "$REPO_ROOT" rev-parse HEAD 2>/dev/null)
    _status_report_completion_marker ready-for-review "$CONFIG_DIR/ready-for-review-markers" "$REPO_HASH_PREFIX" "$READY_FOR_REVIEW_VALUE"

    # verification: same recipe as the `write verification` arm above.
    # Capped and stderr-suppressed like ready-for-review's line: a
    # zero-commit repo has no HEAD^{tree} to hash, which `status` must
    # report as absent rather than error on.
    # No age bound here: `check verification` applies
    # VERIFICATION_CHECK_MAX_AGE_SECONDS, `status` reports hash state only,
    # matching code-review's split above.
    VERIFICATION_VALUE=$(_lib_head_tree_hash capped "$REPO_ROOT")
    _status_report_completion_marker verification "$CONFIG_DIR/verification-markers" "$REPO_HASH_PREFIX" "$VERIFICATION_VALUE"

    # cumulative-review: same recipe as the `write cumulative-review` arm
    # above, via the shared _lib_cumulative_diff_hash. Makes a network round
    # trip (gh pr view) unlike every other line in this report, which are
    # all local/offline. A hash that can't be computed (gh/network failure,
    # a legitimately empty diff -- often because the branch already has a
    # merged PR, no resolvable merge-base, or the 15s cap firing) is treated
    # as empty here rather than aborting -- `status` is a report, not a
    # write. No reconciliation flag: that check is documented
    # (_status_reconciliation_flag above) as applying only to pathspec-hash
    # markers, and the cumulative diff is not staged/unstaged tree state.
    CUMULATIVE_DIFF_HASH_FAILED=0
    CUMULATIVE_REVIEW_VALUE=$(_lib_cumulative_diff_hash "$REPO_ROOT" "$PR_DIFF_SCRIPT") || { CUMULATIVE_REVIEW_VALUE=""; CUMULATIVE_DIFF_HASH_FAILED=1; }
    _status_report_completion_marker cumulative-review "$CONFIG_DIR/cumulative-review-markers" "$REPO_HASH_PREFIX" "$CUMULATIVE_REVIEW_VALUE"
    # A failed computation with a marker on disk would otherwise print as
    # "historical (hash does not match)" -- a confirmed-mismatch claim this
    # code never actually made. Flag that distinction without changing the
    # line above, so a reader can tell "could not verify" apart from
    # "confirmed stale".
    if [ "$CUMULATIVE_DIFF_HASH_FAILED" -eq 1 ] && _status_glob_has_match "$CONFIG_DIR/cumulative-review-markers" "$REPO_HASH_PREFIX"; then
      printf '  cumulative-review: could not verify (pr-diff-against-base.sh failed, the diff is empty -- often because this branch already has a merged PR -- or resolution timed out -- the state above reflects marker presence only, not a confirmed hash comparison)\n' >&2
    fi

    # review-pr: keyed to the main tree's root (_lib_review_pr_marker_repo_hash),
    # not REPO_HASH above, so every tree of the repo reads the same marker.
    # This report is reachable only where the lines above are, because the
    # `status` arm's _resolve_repo_root exits first from the main tree of a
    # worktree-enforced repo that has a linked worktree. The else branch runs
    # only when the main-root lookup or its hash fails after that succeeded.
    if REVIEW_PR_REPO_HASH=$(_lib_review_pr_marker_repo_hash); then
      REVIEW_PR_VALUE=$(_lib_review_pr_provenance_field "$(_lib_review_pr_artifact_path "$CONFIG_DIR" "$SESSION_ID" provenance)" head_ref_oid) || REVIEW_PR_VALUE=""
      _status_report_review_pr_marker "$CONFIG_DIR" "$REVIEW_PR_REPO_HASH" "$SESSION_ID" "$REVIEW_PR_VALUE"
    else
      printf '  review-pr: could not verify (the main tree root or its repo hash could not be resolved)\n' >&2
    fi

    printf '\nActive-bypass markers (this session):\n'
    _status_report_active_bypass plan-review "$(_active_bypass_dir_for plan-review)" "$SESSION_ID"
    _status_report_active_bypass ready-for-review "$(_active_bypass_dir_for ready-for-review)" "$SESSION_ID"
    _status_report_active_bypass respond-pr "$(_active_bypass_dir_for respond-pr)" "$SESSION_ID"
    _status_report_active_bypass memory-skill "$(_active_bypass_dir_for memory-skill)" "$SESSION_ID"
    _status_report_active_bypass handoff "$(_active_bypass_dir_for handoff)" "$SESSION_ID"
    ;;
  check)
    case "$SKILL" in
      code-review)
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        # Same hash recipe as the `write code-review` arm, base and all:
        # both route through _lib_code_review_marker_value, so a marker
        # written under a trusted in-progress base's own base-relative diff
        # can actually be matched here rather than compared against a plain
        # HEAD-relative diff that would never agree with it. Read-only: no
        # SESSION_ID needed since this never writes.
        # A hash that can't be computed (`_lib_code_review_marker_value`
        # returns 1) must read as no-match, not match. This is the
        # short-circuit /code-review consults before skipping its
        # specialist panel, so failing open here would silently skip a real
        # review.
        #
        # Capped unlike `write code-review`'s git calls, since `check` runs
        # on every `/code-review` invocation rather than only on a completed
        # review. A timeout here degrades to no-match, never a false match.
        GATE_DIFF_BASE=$(_lib_gate_diff_base "$REPO_ROOT")
        MARKER_VALUE=$(_lib_code_review_marker_value "$REPO_ROOT" "$GATE_DIFF_BASE") || { printf 'no-match\n'; exit 1; }
        # Checked against whatever _lib_code_review_marker_value itself
        # would produce for GATE_DIFF_BASE's own "no new content" case,
        # computed here rather than hardcoded so no single line embeds the
        # full hex digest. Not a separate `git diff --cached --quiet`
        # probe. Two decoupled git calls can diverge under transient
        # contention (index lock, background git process). A stale marker
        # can then read as a match even though this call already proved the
        # diff empty.
        #
        # This also covers `ready-for-review` step 3's invocation of
        # `/code-review` with nothing staged, where a stale empty-diff
        # marker from an unrelated earlier review could otherwise silently
        # skip that cumulative-diff review.
        if [ -n "$GATE_DIFF_BASE" ]; then
          EMPTY_DIFF_HASH=$(_lib_hash_diff_text "$(_lib_code_review_empty_base_sentinel "$GATE_DIFF_BASE")")
        else
          EMPTY_DIFF_HASH="$_STAGED_DIFF_EMPTY_DIGEST"
        fi
        if [ "$MARKER_VALUE" = "$EMPTY_DIFF_HASH" ]; then
          printf 'no-match\n'
          exit 1
        fi
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # A hash match older than CODE_REVIEW_CHECK_MAX_AGE_SECONDS reads as
        # no-match too. See docs/design-decisions.md for why this age bound
        # applies only here, not to the write/commit-gate side.
        if _lib_marker_value_present "$CONFIG_DIR/code-review-markers" "$MARKER_VALUE" "$REPO_HASH."; then
          _resolve_code_review_check_max_age_seconds
          if FRESH_AGE=$(_marker_fresh_age "$CONFIG_DIR/code-review-markers" "$MARKER_VALUE" "$REPO_HASH." "$CODE_REVIEW_CHECK_MAX_AGE_SECONDS"); then
            printf 'match age_seconds=%s\n' "$FRESH_AGE"
            exit 0
          fi
        fi
        printf 'no-match\n'
        exit 1
        ;;
      verification)
        REPO_ROOT=$(_resolve_repo_root) || exit 2
        # Per-repo opt-in gate (docs/design-decisions/ready-for-review-verification-cache.md):
        # a repo that hasn't committed the sentinel to its default branch
        # never matches, regardless of hash freshness. Checked first so a
        # not-opted-in repo short-circuits before the uncommitted-status and
        # tree-hash calls below.
        _lib_verification_cache_sentinel_present "$REPO_ROOT" || { printf 'no-match\n'; exit 1; }
        # Every no-match exit below is byte-identical to this one.
        # test_marker_script.py's TestMarkerScriptVerification class depends
        # on its _opted_in_origin autouse fixture to reach any of them for
        # the right reason.
        # Same hash recipe as the `write verification` arm. Read-only: no
        # SESSION_ID needed since this never writes.
        # A hash that can't be computed must read as no-match, not match --
        # same fail-closed direction as `check code-review` above.
        #
        # `write verification` refuses to write over uncommitted changes, so
        # a hash match here only means something when the tree is clean too.
        # Unlike `write`, a dirty tree is not an error here -- `check` is
        # read-only, so it just reads as a cache miss.
        # Capped like every other check-path git call in this file, unlike
        # write's own uncapped guard. This call runs on every `check`
        # invocation, so a stall here should degrade, capped-tooling permitting.
        UNCOMMITTED_STATUS=$(_lib_capped git -C "$REPO_ROOT" status --porcelain 2>/dev/null)
        STATUS_EXIT=$?
        [ "$STATUS_EXIT" -eq 0 ] && [ -z "$UNCOMMITTED_STATUS" ] || { printf 'no-match\n'; exit 1; }
        MARKER_VALUE=$(_lib_head_tree_hash capped "$REPO_ROOT") || { printf 'no-match\n'; exit 1; }
        REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
        # A hash match older than VERIFICATION_CHECK_MAX_AGE_SECONDS reads as
        # no-match too. See docs/design-decisions/ready-for-review-verification-cache.md
        # for why this age bound applies only here, not to the write side.
        if _lib_marker_value_present "$CONFIG_DIR/verification-markers" "$MARKER_VALUE" "$REPO_HASH."; then
          _resolve_verification_check_max_age_seconds
          if FRESH_AGE=$(_marker_fresh_age "$CONFIG_DIR/verification-markers" "$MARKER_VALUE" "$REPO_HASH." "$VERIFICATION_CHECK_MAX_AGE_SECONDS"); then
            printf 'match age_seconds=%s\n' "$FRESH_AGE"
            exit 0
          fi
        fi
        printf 'no-match\n'
        exit 1
        ;;
      *)
        printf "marker.sh: 'check %s' is not valid. 'check' supports: code-review, verification\n" "$SKILL" >&2
        exit 2
        ;;
    esac
    ;;
esac

fi
