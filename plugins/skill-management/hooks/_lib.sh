#!/bin/bash
# Trimmed shared helper library for skill-management plugin hooks.
# Source this file (do NOT invoke it). Contains the helpers needed by
# require-skill-review.sh: _lib_config_dir, _lib_jq, _lib_capped_for,
# _lib_parse_tool_input_or_deny, _marker_lib_repo_hash,
# _lib_marker_value_present, _lib_chains_marker_write_before_commit,
# _lib_capped, _lib_default_branch_from_origin_head,
# _lib_default_branch_or_guess, _lib_git_inprogress_state,
# _lib_gate_diff_base, _lib_staged_diff_hash,
# _lib_skill_review_diff_base, and _lib_conflict_marker_deny_paths. No
# worktree-enforcement helpers.
#
# _lib_config_dir and _marker_lib_repo_hash must stay byte-identical to the
# same functions in the stowed claude/.claude/hooks/_lib.sh. marker.sh (the
# write side) always sources that stowed copy directly
# ($HOME/.claude/hooks/_lib.sh), never a plugin-bundled one. A divergence
# here would break the config-directory resolution or the repo-hash key
# shared between the write side and this hook's read side.
# _lib_marker_value_present is duplicated from that same file for the same
# reason the others are: a plugin cannot source across the plugin boundary.
# _lib_capped, _lib_default_branch_from_origin_head,
# _lib_default_branch_or_guess, _lib_git_inprogress_state,
# _lib_gate_diff_base, _lib_staged_diff_hash, and
# _lib_skill_review_diff_base carry the same byte-identical-body contract:
# require-skill-review.sh's novel-content base and marker preimage must
# match scripts/marker.sh's write side exactly, or a marker written under
# one recipe can never match the gate reading it under the other.

# Prints the active Claude Code config directory: $CLAUDE_CONFIG_DIR if set
# (must be absolute — a relative value resolves differently per invocation
# cwd, the same path-mismatch bug this function exists to fix), else
# $HOME/.claude. Returns 1 with no stdout when CLAUDE_CONFIG_DIR is relative,
# or when CLAUDE_CONFIG_DIR is unset/empty and $HOME is also unset/empty.
# Call-site contract (load-bearing): bare interpolation,
# "$(_lib_config_dir)/whatever", is unsafe — under `set -e`, a failing
# *nested* command substitution does not abort the script, so a resolver
# failure silently collapses to "/whatever" (root-anchored) instead of being
# caught. Every call site must capture and check the exit status first:
#   config_dir=$(_lib_config_dir) || { <fail-open-or-deny per this caller>; }
_lib_config_dir() {
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    case "$CLAUDE_CONFIG_DIR" in
      /*) ;;
      *) return 1 ;;  # relative values resolve differently per invocation
                      # cwd — the exact read/write path-mismatch bug this
                      # function fixes, just triggered a different way.
    esac
    printf '%s\n' "${CLAUDE_CONFIG_DIR%/}"
    return 0
  fi
  local home_norm="${HOME%/}"
  [ -n "$home_norm" ] || return 1
  printf '%s\n' "$home_norm/.claude"
}

# Backstop against a hung jq (~5s, not a per-fire latency budget).
# Cites guard-settings-session-keys.sh's git_capped 5s precedent.
# Fallback to bare jq when timeout(1) is absent (BSD/macOS default).
# install.sh warns about missing timeout at onboarding time.
# Security implication: on BSD/macOS without coreutils, a stalled or
# replaced jq binary can hold a gate hook open until the harness's own hook
# timeout, which does not block the tool call (see _lib_capped_for below).
_lib_jq() {
  if command -v timeout >/dev/null 2>&1; then
    timeout 5 jq "$@"
  else
    jq "$@"
  fi
}

# _lib_capped_for SECONDS CMD [ARGS...]
# Duplicated from claude/.claude/hooks/_lib.sh's function of the same name
# (see this file's header for why plugin hooks duplicate rather than source).
# Probes timeout(1) then gtimeout(1); runs CMD uncapped when neither exists.
# Callers MUST check the exit status and fail closed on every status they do
# not recognise.
# `-k 2` escalates to SIGKILL 2s after the SIGTERM, so the wrapper returns at
# SECONDS+2 even when the direct child ignores SIGTERM.
# The bound covers the direct child only. A descendant that holds a captured
# stdout pipe keeps a `$(...)` caller open, including a nested capped call:
# GNU timeout puts its child in a new process group unless run with
# --foreground, so the outer cap's group-directed signal does not reach it.
# On the uncapped fallback, or with a child that survives SIGKILL, a stalled
# PreToolUse gate exits only by the harness's own hook timeout, which does not
# block the tool call (hooks reference, Timeouts section, fetched 2026-09-19).
# The short `-k 2` spelling is load-bearing: BusyBox's timeout rejects
# `--kill-after=2` with a usage error and never runs the command.
# The requirement is a `timeout` or `gtimeout` that accepts `-k`: GNU coreutils,
# or BusyBox 1.35.0 or newer.
# One that rejects `-k` makes every wrapped call exit nonzero without running
# CMD, so the SKILL.md commit denies.
# The claude-config repository's README.md Requirements section is the
# canonical statement, including the builds not verified here.
# When the cap fires, the wrapper exits 124 (GNU) or 143 (BusyBox) if SIGTERM
# killed CMD, and 137 (both) if the -k grace escalated to SIGKILL.
# 137 and 143 also occur as CMD's own signal-death status, so a status alone
# cannot prove the cap fired.
# No capped CMD may trap TERM and exit 0, because BusyBox would then return
# that 0 where GNU returns 124.
_lib_capped_for() {
  local seconds="${1:?_lib_capped_for requires a seconds argument}"
  shift
  if command -v timeout >/dev/null 2>&1; then
    timeout -k 2 "$seconds" "$@"
  elif command -v gtimeout >/dev/null 2>&1; then
    gtimeout -k 2 "$seconds" "$@"
  else
    "$@"
  fi
}

# Same backstop and same probe-then-fallback as _lib_jq, for any other
# command that reads the filesystem and can stall on it (git against a
# locked .git/index, sha256sum against a dead NFS mount). Callers MUST check
# the exit status: a bare `timeout 5 git ...` is not just uncapped when
# timeout(1) is missing, it is "command not found" (127), which silently
# yields empty output on stock macOS.
# Usage: out=$(_lib_capped git -C "$root" ls-files ...) || <fail closed>
_lib_capped() {
  _lib_capped_for 5 "$@"
}

# _lib_default_branch_from_origin_head REPO_ROOT
# Resolve REPO_ROOT's default branch from the local symbolic ref
# refs/remotes/origin/HEAD alone, verifying the target actually resolves to
# a commit before returning it.
# Local refs only, never the network -- callers use the result as a local ref immediately.
# `--quiet symbolic-ref`, not `rev-parse --abbrev-ref origin/HEAD`: the
# latter never returns empty, which would mask an unset origin/HEAD.
# Two-outcome contract:
#   - exit 0, non-empty stdout: the resolved default branch name.
#   - exit 1, empty stdout: origin/HEAD is unset, dangling, or the call
#     timed out. Callers decide their own fallback.
_lib_default_branch_from_origin_head() {
  local repo_root="$1"
  local ref default_branch
  ref=$(_lib_capped git -C "$repo_root" symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null)
  [ -n "$ref" ] || return 1
  _lib_capped git -C "$repo_root" rev-parse --verify --quiet "$ref" >/dev/null 2>&1 || return 1
  default_branch="${ref#refs/remotes/origin/}"
  [ -n "$default_branch" ] || return 1
  printf '%s' "$default_branch"
}

# _lib_default_branch_or_guess REPO_ROOT
# Resolve REPO_ROOT's default branch: first via
# _lib_default_branch_from_origin_head, then, on its failure, by falling
# through to probing conventional candidate names (main, master, develop)
# against existing origin/<candidate> refs. Designed to be shared by every
# caller whose wrong-answer consequence is a diff, a message, or a withheld
# gate rather than the base it fetches, rebases onto, or deletes against
# (docs/design-decisions.md §54).
# Local refs only, never the network -- callers use the result as a local ref immediately.
# Candidate order is a prior, not a guarantee: with origin/HEAD unset and
# several candidate refs present, the first match wins even when it is stale.
# Two-outcome contract:
#   - exit 0, non-empty stdout: the resolved default branch name.
#   - exit 1, empty stdout: neither the symbolic ref nor any candidate
#     resolved. Callers decide their own fallback -- this helper does not
#     pick a fail posture.
_lib_default_branch_or_guess() {
  local repo_root="$1"
  local default_branch candidate
  if default_branch=$(_lib_default_branch_from_origin_head "$repo_root"); then
    printf '%s' "$default_branch"
    return 0
  fi
  for candidate in main master develop; do
    if _lib_capped git -C "$repo_root" rev-parse --verify "origin/$candidate" >/dev/null 2>&1; then
      printf '%s' "$candidate"
      return 0
    fi
  done
  return 1
}

# _lib_git_inprogress_state REPO_ROOT [GITDIR]
# Detects which git operation, if any, is paused mid-way in REPO_ROOT:
# rebase, merge, cherry-pick, or revert. Precedence is checked in that
# order, matching git-state-safety/SKILL.md's own rule-of-thumb ordering.
# GITDIR is optional: when the caller has already resolved
# `--absolute-git-dir` for its own purposes (_lib_gate_diff_base does), pass
# it here to skip this function's own resolution and avoid spawning git
# twice for the same answer. Omit it to have this function resolve gitdir
# itself.
# Detection recipe per state (`git rev-parse --absolute-git-dir` resolved
# once, then plain file tests against it -- not `git rev-parse --git-path`
# per state, which would spawn git four times for the same answer):
#   rebase       rebase-merge/ or rebase-apply/ dir exists
#   merge        MERGE_HEAD exists
#   cherry-pick  CHERRY_PICK_HEAD exists
#   revert       REVERT_HEAD exists
# --absolute-git-dir (not --git-dir) resolves a linked worktree's own
# per-worktree gitdir rather than the shared main one, which is where these
# five markers actually live.
#
# Tri-state via exit status:
#   - exit 0, stdout = one of rebase/merge/cherry-pick/revert: that state is
#     in progress.
#   - exit 1, stdout empty: no in-progress state.
#   - exit 2, stdout empty: the gitdir could not be resolved (or, when
#     GITDIR was passed in, it was empty) -- the underlying _lib_capped call
#     timed out, was killed, or git itself was missing. Callers MUST NOT
#     treat this as "no state" -- see _lib_gate_diff_base below, whose
#     safety property depends on this status never being read as a green
#     light.
_lib_git_inprogress_state() {
  [ "$#" -eq 1 ] || [ "$#" -eq 2 ] || return 2
  local repo_root="$1"
  local gitdir="${2:-}"
  if [ -z "$gitdir" ]; then
    if ! gitdir=$(_lib_capped git -C "$repo_root" rev-parse --absolute-git-dir 2>/dev/null); then
      return 2
    fi
  fi
  [ -n "$gitdir" ] || return 2
  if [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ]; then
    printf '%s' rebase
    return 0
  fi
  if [ -f "$gitdir/MERGE_HEAD" ]; then
    printf '%s' merge
    return 0
  fi
  if [ -f "$gitdir/CHERRY_PICK_HEAD" ]; then
    printf '%s' cherry-pick
    return 0
  fi
  if [ -f "$gitdir/REVERT_HEAD" ]; then
    printf '%s' revert
    return 0
  fi
  return 1
}

# _lib_gate_diff_base REPO_ROOT
# Prints the tree-ish a commit-time gate should diff its staged content
# against, in place of the index's implicit HEAD base -- so a gate that
# hashes or scans `git diff --cached "$(_lib_gate_diff_base "$repo")"` sees
# only content novel to the commit being made, even mid-merge. Outside an
# in-progress state (the overwhelming common case) this prints nothing, and
# the call site issues plain `git diff --cached` with no base argument.
# During a trusted in-progress state, this computes the tree git's own
# automatic merge/rebase/cherry-pick/revert machinery would have produced,
# via `git merge-tree --write-tree`. See the claude-config repository's
# docs/design-decisions/merge-tree-base-recipe-for-gate-diff-base.md for the
# per-state command table, the trust-anchor definitions and
# admissibility argument, the fallback-case enumeration, and the
# OID-shape and merge-tree-output validation this function applies before
# trusting either.
#
# Tri-state via exit status, same contract as _lib_git_inprogress_state:
#   - exit 0, stdout = a tree OID: an in-progress state was detected, its
#     OID reached a trusted anchor, and the reference tree was computed.
#   - exit 1, stdout empty: no in-progress state, or one whose OID reached
#     no anchor, or a topology/git-version this design computes no base
#     for. This is the correct answer, not a degraded one.
#   - exit 2, stdout empty: undetermined -- a capped git call inside
#     detection or tree computation timed out, was killed, or its binary
#     was missing. Every caller consumes stdout unconditionally regardless
#     of exit status, so this must stay empty on status 2; see the
#     design-decision doc for the full load-bearing safety argument.
_lib_gate_diff_base() {
  [ "$#" -eq 1 ] || return 2
  local repo_root="$1"

  local gitdir
  gitdir=$(_lib_capped git -C "$repo_root" rev-parse --absolute-git-dir 2>/dev/null) || return 2
  [ -n "$gitdir" ] || return 2

  local state state_status
  state=$(_lib_git_inprogress_state "$repo_root" "$gitdir")
  state_status=$?
  [ "$state_status" -eq 2 ] && return 2
  [ "$state_status" -eq 1 ] && return 1

  local ref_file
  case "$state" in
    rebase) ref_file="REBASE_HEAD" ;;
    merge) ref_file="MERGE_HEAD" ;;
    cherry-pick) ref_file="CHERRY_PICK_HEAD" ;;
    revert) ref_file="REVERT_HEAD" ;;
    *) return 2 ;;
  esac
  [ -f "$gitdir/$ref_file" ] || return 1
  local state_oid
  state_oid=$(_lib_capped cat "$gitdir/$ref_file" 2>/dev/null)
  [ -n "$state_oid" ] || return 1
  [[ "$state_oid" =~ ^[0-9a-f]{40}$ ]] || [[ "$state_oid" =~ ^[0-9a-f]{64}$ ]] || return 1

  local default_branch anchor_reached=1
  default_branch=$(_lib_default_branch_or_guess "$repo_root")
  if [ -n "$default_branch" ] \
    && _lib_capped git -C "$repo_root" merge-base --is-ancestor "$state_oid" "refs/remotes/origin/$default_branch" >/dev/null 2>&1
  then
    anchor_reached=0
  elif _lib_capped git -C "$repo_root" merge-base --is-ancestor "$state_oid" HEAD >/dev/null 2>&1; then
    anchor_reached=0
  fi
  [ "$anchor_reached" -eq 0 ] || return 1

  local tree_out tree_status
  case "$state" in
    merge)
      tree_out=$(_lib_capped git -C "$repo_root" merge-tree --write-tree HEAD "$state_oid" 2>/dev/null)
      tree_status=$?
      ;;
    rebase | cherry-pick)
      tree_out=$(_lib_capped git -C "$repo_root" merge-tree --write-tree "--merge-base=${state_oid}^" HEAD "$state_oid" 2>/dev/null)
      tree_status=$?
      ;;
    revert)
      tree_out=$(_lib_capped git -C "$repo_root" merge-tree --write-tree "--merge-base=${state_oid}" HEAD "${state_oid}^" 2>/dev/null)
      tree_status=$?
      ;;
    # Unreachable: the earlier case in this function already validates
    # $state against these same four values with its own `*) return 2`.
    *) return 2 ;;
  esac
  case "$tree_status" in
    124 | 125 | 126 | 127 | 137 | 143) return 2 ;;
  esac
  # tree_status is not the validation signal. `merge-tree --write-tree`
  # exits 1 (not 0) whenever the merge it computed conflicts -- the
  # expected, common case here, since resolving that conflict is the whole
  # reason this function exists. It still writes a valid tree, with
  # embedded conflict markers, on its first stdout line. Whether that first
  # line resolves to a real tree is the actual check, below.
  local tree_oid="${tree_out%%$'\n'*}"
  [ -n "$tree_oid" ] || return 1

  _lib_capped git -C "$repo_root" rev-parse --verify --quiet "${tree_oid}^{tree}" >/dev/null 2>&1
  local verify_status=$?
  case "$verify_status" in
    124 | 125 | 126 | 127 | 137 | 143) return 2 ;;
  esac
  [ "$verify_status" -eq 0 ] || return 1

  printf '%s' "$tree_oid"
}

# _lib_skill_review_diff_base REPO_ROOT
# Returns the same tri-state contract as _lib_gate_diff_base, except mid-revert
# returns 1, empty stdout, in place of the synthesized tree.
# A revert's synthesized tree is HEAD minus a reviewed patch, so its removals
# were reviewed nowhere.
# A pre-sample reading no in-progress state returns 1 directly, without
# calling _lib_gate_diff_base, the same answer _lib_gate_diff_base itself
# gives on the same resolved gitdir.
# The state is sampled once before and once after the _lib_gate_diff_base
# call, not only after, because both reads hit the same mutable gitdir.
_lib_skill_review_diff_base() {
  [ "$#" -eq 1 ] || return 2
  local repo_root="$1"

  local gitdir
  gitdir=$(_lib_capped git -C "$repo_root" rev-parse --absolute-git-dir 2>/dev/null) || return 2
  [ -n "$gitdir" ] || return 2

  local pre_state pre_status
  pre_state=$(_lib_git_inprogress_state "$repo_root" "$gitdir")
  pre_status=$?
  # Unreachable: gitdir is verified non-empty above, and _lib_git_inprogress_state
  # returns 2 on a passed-in GITDIR only when it is empty.
  [ "$pre_status" -eq 2 ] && return 2
  [ "$pre_status" -eq 1 ] && return 1
  [ "$pre_state" = revert ] && return 1

  local base base_status
  base=$(_lib_gate_diff_base "$repo_root")
  base_status=$?
  [ "$base_status" -eq 0 ] || return "$base_status"

  local post_state post_status
  post_state=$(_lib_git_inprogress_state "$repo_root" "$gitdir")
  post_status=$?
  # Unreachable: same gitdir, already verified non-empty above.
  [ "$post_status" -eq 2 ] && return 2
  [ "$post_status" -eq 0 ] && [ "$post_state" = revert ] && return 1

  printf '%s' "$base"
}

# _lib_staged_diff_hash REPO_ROOT BASE [PATHSPEC...]
# Shared body behind every content-addressed marker preimage and round-state
# value in this file and in scripts/marker.sh: sha256 of `git diff --cached`,
# restricted to PATHSPEC when given. BASE is already resolved by the caller
# (typically via _lib_gate_diff_base) -- this function has no way to
# recompute one, which is deliberate: the write side (marker.sh) and every
# read side must hash byte-identical input, and threading an
# already-resolved value through the signature makes that structural rather
# than a discipline each of the seven call sites has to remember.
# BASE empty means no override: this runs plain `git diff --cached
# [-- PATHSPEC...]`. BASE non-empty means `git diff --cached "$BASE"
# [-- PATHSPEC...]`.
# Two-outcome contract: exit 0 with the hex digest on stdout, or exit 1 with
# empty stdout when git or sha256sum failed or was killed. sha256 of even an
# empty diff is a non-empty 64-hex digest, so stdout alone can't distinguish
# "nothing staged" from a failed pipeline -- what actually distinguishes them
# is the capped `git diff` call's own exit status, captured via
# `${PIPESTATUS[0]}` in the same command substitution immediately after the
# pipeline runs (before any later command in that subshell can overwrite
# it): any nonzero status there -- an ordinary git error or a cap kill
# (statuses per _lib_capped_for's header) alike -- fails this closed regardless of whether
# sha256sum/awk still produced output downstream. Callers must fail closed
# on exit 1: a marker preimage that treats a failed hash as "empty diff"
# would authorize release on content nobody reviewed.
# Fails closed on an empty REPO_ROOT rather than letting `git -C ""` resolve
# relative to the caller's cwd -- every current call site already validates
# REPO_ROOT, but this is a shared primitive future callers may not.
# A no-op GIT_EXTERNAL_DIFF/diff.external driver makes a genuinely-staged
# change hash as empty here, an accepted residual pinned by
# test_git_external_diff_noop_misclassifies_staged_skill_content_as_empty
# in test_marker_script.py.
_lib_staged_diff_hash() {
  [ "$#" -ge 2 ] || return 1
  local repo_root="$1" base="$2"
  [ -n "$repo_root" ] || return 1
  shift 2
  local -a diff_args=(-C "$repo_root" diff --cached)
  [ -n "$base" ] && diff_args+=("$base")
  [ "$#" -gt 0 ] && diff_args+=(-- "$@")
  local out git_status digest
  out=$(
    _lib_capped git "${diff_args[@]}" 2>/dev/null | sha256sum | awk '{print $1}'
    printf '\n%s' "${PIPESTATUS[0]}"
  )
  git_status="${out##*$'\n'}"
  digest="${out%$'\n'*}"
  digest="${digest%$'\n'}"
  [ "$git_status" -eq 0 ] || return 1
  [ -n "$digest" ] || return 1
  printf '%s' "$digest"
}

# Reads stdin into INPUT (global), extracts TOOL_NAME and COMMAND (globals)
# via a single _lib_jq call using ASCII Unit Separator (0x1f) as delimiter.
# The single call surfaces a structural-type error when .tool_input is non-object
# (jq non-zero exit).
#
# Three deny paths protect against silent-allow:
#   (a) jq non-zero exit (parse failure, timeout exit=124 on GNU, missing jq binary)
#   (b) empty INPUT (stdin EOF, closed pipe, harness misbehavior)
#   (c) empty TOOL_NAME (valid JSON but PreToolUse contract not honored, e.g. "{}")
# Per Anthropic PreToolUse contract, every legitimate event has a non-empty
# .tool_name; absence indicates the call did not originate from a real tool
# invocation. Without (b)/(c), downstream gates that early-exit on
# `[ "$TOOL_NAME" = "Bash" ]` silently allow on zero-byte or "{}" inputs.
#
# Contract: if this function returns, parse succeeded AND TOOL_NAME is set.
# On failure, calls emit_deny (caller-defined) with the supplied message and
# exits 0 — caller never checks $?. Never call this with `|| true` or in
# a pipeline. CALLER MUST define emit_deny before sourcing _lib.sh so this
# helper can resolve it; the canonical pattern (define-emit_deny-then-source)
# is enforced by test_hook_alignment.py.
_lib_parse_tool_input_or_deny() {
  local deny_msg="${1:-Blocked: could not parse tool-input JSON.}"
  INPUT=$(cat)  # command substitution strips trailing newlines — safe for JSON payloads
  if [ -z "$INPUT" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  # Single jq call extracts both fields delimited by ASCII Unit Separator (0x1f)
  # rather than newlines, preventing a tool_name value containing an embedded
  # newline from corrupting COMMAND via head/tail line splitting. Unit Separator
  # cannot appear in a valid Claude Code tool name or shell command.
  # The .tool_input.command extraction additionally surfaces a structural-type
  # error when .tool_input is non-object (e.g. "Cannot index string with string
  # 'command'"), returning non-zero.
  local jq_out
  # WARNING: the format string below contains a literal 0x1f (ASCII Unit Separator)
  # byte between the two interpolated fields - invisible in editors and diff views.
  # Do not remove it. test_lib.py::test_valid_bash_payload_returns_ok will fail
  # immediately if the delimiter is absent, catching accidental deletion.
  jq_out=$(printf '%s\n' "$INPUT" | _lib_jq -r '"\(.tool_name // "")\(.tool_input.command // "")"' 2>/dev/null)
  local jq_exit=$?
  if [ "$jq_exit" -ne 0 ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  TOOL_NAME="${jq_out%%$'\x1f'*}"
  # shellcheck disable=SC2034 # set for hook scripts that source this file and reference $COMMAND
  COMMAND="${jq_out#*$'\x1f'}"
  # Embedded newline in TOOL_NAME means the payload violated the PreToolUse
  # contract; deny rather than allow with a corrupted TOOL_NAME value.
  if [ -z "$TOOL_NAME" ]; then
    emit_deny "$deny_msg"
    exit 0
  fi
  case "$TOOL_NAME" in
    *$'\n'*) emit_deny "$deny_msg"; exit 0 ;;
  esac
}

# Compute the marker repo-hash for an absolute repo-toplevel path.
# Input must have no trailing newline -- printf '%s' omits one, so the SHA
# covers exactly the bytes of $1.
# Exit 1, empty stdout: sha256sum/awk produced no output (tool misbehavior).
# Usage: hash=$(_marker_lib_repo_hash "$REPO_ROOT")
_marker_lib_repo_hash() {
  local digest
  digest=$(printf '%s' "$1" | sha256sum | awk '{print $1}')
  [ -n "$digest" ] || return 1
  printf '%s\n' "$digest"
}

# _lib_marker_value_present MARKERS_DIR EXPECTED_VALUE GLOB_PREFIX...
# Returns 0 (true) iff some file in MARKERS_DIR whose name begins with one of
# the supplied prefixes holds EXPECTED_VALUE as a whole line.
#
# Completion markers are content-addressed: the stored value is a hash of
# exactly the state that was reviewed. That content is the authorization, so
# the read asks "has this state been reviewed?" rather than "did this filename
# review it?" — the filename keys only namespace concurrent writers apart.
#
# One `grep` process regardless of marker count. Marker directories are
# unbounded and already hold thousands of entries, so a per-file `cat` loop
# would put a fork count proportional to review history on a hook that fires
# on every git commit.
#
# `-x` (whole-line) is load-bearing, not stylistic: with a bare `-F` a stored
# value that merely CONTAINS the expected hash — a truncated write, or a
# longer digest sharing this one as a prefix — would falsely release the gate.
#
# `nullglob` is equally load-bearing. Bash's default expands a zero-match
# pattern to the literal, unexpanded pattern string, which grep then tries to
# open as a real path and fails on (exit 2) — and "no marker exists yet for
# this prefix" is the single most common call, so the default behavior would
# misreport the common case as an error rather than as a clean not-found.
# The shopt state is saved and restored so callers that rely on the default
# glob behavior elsewhere are unaffected.
#
# The function BODY is kept byte-identical to the same function in the stowed
# claude/.claude/hooks/_lib.sh; see the header note on why this file duplicates
# rather than sources. The comment block above it intentionally differs (it
# names this file's own consumer), so a drift check must diff the body, not the
# whole block — a wholesale diff reports expected comment divergence and would
# bury a real body drift in the noise. test_require_skill_review.py asserts the
# body parity mechanically.
_lib_marker_value_present() {
  local markers_dir="$1" expected_value="$2"
  shift 2
  [ -n "$markers_dir" ] || return 1
  [ -n "$expected_value" ] || return 1
  [ -d "$markers_dir" ] || return 1
  [ "$#" -gt 0 ] || return 1

  local nullglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  shopt -s nullglob
  local -a marker_files=()
  local prefix
  for prefix in "$@"; do
    [ -n "$prefix" ] || continue
    marker_files+=("$markers_dir/$prefix"*)
  done
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi

  [ "${#marker_files[@]}" -gt 0 ] || return 1
  # -e pins EXPECTED_VALUE as the pattern even if it begins with a dash; the
  # stderr redirect swallows the "Is a directory" noise a stray subdirectory
  # under MARKERS_DIR would otherwise produce.
  #
  # SCALE BOUNDARY: the matched files become one argv, so a single prefix
  # holding roughly 13k-30k markers (host-dependent; ARG_MAX bounded by the
  # stack rlimit) makes grep fail to exec with E2BIG. That is a nonzero exit,
  # which every call site reads as "no matching marker" — so it fails CLOSED,
  # denying rather than releasing. Completion markers are never pruned today
  # (marker.sh clear-stale only evicts active-bypass markers), so the ceiling
  # is reachable by unattended growth. Whoever adds marker retention should
  # remove this note; until then a wedged gate at that scale is a deny with no
  # diagnostic, and manual pruning of ~/.claude/*-markers/ is the workaround.
  # A flat glob (not `grep -r`) is deliberate: recursion would let a file
  # nested in a stray subdirectory authorize a gate.
  #
  # Callers must test truthiness, never `[ $? -eq 1 ]`: grep reports a plain
  # no-match as 1 but returns 2 when any argv entry errors — which a stray
  # subdirectory under MARKERS_DIR ("Is a directory") triggers even under -q.
  # Both are correctly false here; an exact-code check would not be.
  grep -qFx -e "$expected_value" -- "${marker_files[@]}" 2>/dev/null
}

# Decide whether a command chains `marker.sh write <skill>` before its first
# `git commit`. PreToolUse hooks fire once per Bash tool call before the chain
# runs, so an on-disk marker check denies naturally-typed forms like
# `marker.sh write code-review && git commit`. When the same Bash call will
# write the marker before invoking commit, the in-chain marker.sh invocation
# is the same evidence the on-disk marker would later provide — marker.sh is
# the only sanctioned writer in either case.
#
# **Anchored at command start, not fragment start.** A fragment-walking
# approach (split on `&&` / `;` / `|`, then scan each fragment) would treat
# heredoc-body lines and wrapper-command arguments as "fragments" — so
# `echo ~/.claude/scripts/marker.sh write code-review && git commit` or
# `cat <<EOF | bash\n...marker.sh write code-review\nEOF && git commit` would
# wedge the gate open without ever actually invoking marker.sh. The strict
# command-start anchor here mirrors enforce-marker-script-shape.sh's
# VALID_CHAINED_COMMIT_PATTERN: only the literal shape
# `marker.sh write <skill>{,&& marker.sh write <skill2>...} && git commit`
# is honored. Wrapper commands, env-var prefixes, `bash -c`, heredoc bodies,
# and pipes all fail the anchor.
#
# Usage: _lib_chains_marker_write_before_commit "$COMMAND" code-review
# Returns 0 (true) if the command matches the sanctioned chained shape AND
# the target skill appears among the chained writes.
_lib_chains_marker_write_before_commit() {
  local command="$1" skill="$2"
  # Step 1: command matches the sanctioned chained shape (mirrors
  # enforce-marker-script-shape.sh's VALID_CHAINED_COMMIT_PATTERN). One or
  # more marker.sh write fragments joined by `&&`, then git commit. Anchored
  # so wrapper commands cannot trick the gate.
  if ! printf '%s' "$command" | grep -qE \
    "^[[:space:]]*((~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)[[:space:]]*&&[[:space:]]*)+git[[:space:]]+commit([[:space:]].*)?$"; then
    return 1
  fi
  # Step 2: target skill is among the chained writes. A chain like
  # `marker.sh write skill-review && git commit` must not authorize a
  # code-review-gated commit.
  printf '%s' "$command" | grep -qE \
    "(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+${skill}([[:space:]]|$)"
}

# _lib_conflict_marker_deny_paths CANDIDATES MARKER_FREE
# Set difference of two newline-delimited path listings: every line in
# CANDIDATES that is not also a line in MARKER_FREE, matched by exact line,
# never substring -- a CANDIDATES entry that is merely a suffix or prefix of
# a MARKER_FREE entry (e.g. a clean `plugins/p/skills/x/SKILL.md` next to a
# conflicted `skills/x/SKILL.md`) still counts as a deny path. Pure string
# logic, no git or filesystem access -- require-skill-review.sh's conflict-marker
# scan is the sole caller, pairing this with its own `git diff -G` /
# `git grep -L` calls that produce the two listings.
# Empty CANDIDATES prints nothing (no false deny from an empty diff). Empty
# MARKER_FREE returns CANDIDATES unchanged (nothing was cleared as clean).
# Prints the newline-delimited result with no trailing newline, or nothing
# when every candidate is marker-free. Always exits 0.
_lib_conflict_marker_deny_paths() {
  local candidates="$1" marker_free="$2"
  local deny_paths=""
  local candidate_path
  [ -n "$candidates" ] || return 0
  while IFS= read -r candidate_path; do
    case $'\n'"$marker_free"$'\n' in
      *$'\n'"$candidate_path"$'\n'*) ;;
      *) deny_paths+="${deny_paths:+$'\n'}$candidate_path" ;;
    esac
  done <<< "$candidates"
  printf '%s' "$deny_paths"
}
