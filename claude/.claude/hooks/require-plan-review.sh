#!/bin/bash
# hook-class: gate
# PreToolUse hook: block Write/Edit/ExitPlanMode when an uncommitted or modified
# plan file exists in .claude/plans/ and no plan-review marker covering that
# exact plan state can be found.
#
# Globally applied (no opt-in), consistent with require-code-review.sh,
# require-ready-for-review.sh, and require-respond-pr.sh.
# Projects without a .claude/plans/ directory, or where all plan files are
# committed and unmodified (historical), pass through silently — the
# uncommitted-plan check is the built-in filter.
#
# Two-marker pattern:
# - Active marker (~/.claude/.plan-review-active.d/<session_id>):
#   content = Claude session PID. Written by /plan-review at step 0;
#   removed at the deactivation step. Bypasses the gate so the skill's own
#   Write/Edit calls during review don't self-deny; ExitPlanMode is excluded
#   from this bypass — an active marker means review is in progress, not
#   complete, so plan presentation stays blocked. The hook checks PID
#   liveness (kill -0) on each gate hit; dead PIDs are evicted automatically,
#   which handles orphaned markers from sessions that errored before cleanup.
# - Completion marker (~/.claude/plan-review-markers/<repo-hash>.<session_id>):
#   written by /plan-review when the review is clean. Content is the
#   sha256 hash of the active plan file set (paths + contents), computed by
#   _lib_active_plan_hash in _lib.sh. Content-addressed, not
#   existence-checked: this hook recomputes the same hash at gate time and
#   allows only on an exact match, so editing a reviewed plan (including a
#   ledger row) re-arms the gate on the next Write/Edit/ExitPlanMode.
#   Mirrors require-code-review.sh's staged-diff-hash marker.
#   When an active plan cannot be hashed at all (unreadable, vanished),
#   _lib_active_plan_hash exits non-zero and this hook denies with a
#   repair-the-file message rather than allowing — an unhashable plan is
#   an unknown review state, not an absent one.
#   The <session_id> in the filename is a WRITE-side key only: it keeps
#   parallel sessions from overwriting each other's markers. The read below
#   globs across it, because the stored hash — not the filename — is what
#   proves the review covered this state. Reading the session key as an
#   authorization predicate denies a resumed session (new session_id) a review
#   it already completed.
#
# Defense-in-depth: the hook filters its own input by tool name; do not
# rely solely on the settings.json matcher condition.
#
# Exit codes:
#   0      — allow (no opinion)
#   0+JSON — deny (plan is active and no marker holds its current hash)

set -uo pipefail

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by plan-review gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by plan-review gate: could not parse tool-input JSON."

# Gate Write, Edit, MultiEdit, and ExitPlanMode tool calls.
case "$TOOL_NAME" in
  Write|Edit|MultiEdit|ExitPlanMode) ;;
  *) exit 0 ;;
esac

# Extract target path immediately after parsing — used both for the
# repo-scope guard below and the deny gate.
TARGET_PATH=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.file_path // empty')

# Resolve the repo from the payload's cwd rather than this hook process's
# ambient cwd, so the marker is keyed to the tree the session is working in.
# Downstream hashing already threads this root (_lib_active_plan_hash), so
# root resolution is the only site that needs converting here.
CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty' 2>/dev/null)
[ -z "$CWD" ] && CWD="$PWD"

# Not in a git repo — can't check for plan files or key the marker.
REPO_ROOT=$(git -C "$CWD" rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# Compute the content-addressed hash of the active plan file set (paths +
# contents; see _lib_active_plan_hash in _lib.sh for the full contract). A
# plan file that is tracked and identical to HEAD is historical (its PR
# shipped) and does not contribute to the hash. Empty result means no plan
# is active -- gate disarmed, covering both an absent .claude/plans/ and one
# containing only historical plans.
# Keep this a top-level assignment. Inside a function, `local VAR=$(...)`
# reports `local`'s exit status (always 0) and would mask the failure; a
# refactor that moves this must split the declaration from the assignment.
if ! CURRENT_HASH=$(_lib_active_plan_hash "$REPO_ROOT"); then
  # A plan is active but could not be hashed; stdout carries the offending
  # path. Fail closed. This deny is deliberately worded differently from the
  # missing-marker deny below: telling the user to run /plan-review here
  # would be circular, since marker.sh hits the identical condition and
  # aborts. This hook gates only Write/Edit/MultiEdit/ExitPlanMode, so Bash
  # stays available to repair the file — point at that escape hatch.
  emit_deny "Blocked by plan-review gate: cannot read the active plan file '$CURRENT_HASH', so the gate cannot tell whether the plan has been reviewed.

This is not a missing review — running /plan-review will fail the same way, because it hashes the same file. Repair the file first, using a Bash command (this gate does not block Bash):

  - Unreadable due to permissions → chmod +r '$CURRENT_HASH'
  - A broken symlink, or a stale file you no longer need → rm '$CURRENT_HASH'
  - Belongs elsewhere → mv it out of .claude/plans/

Then retry. If the file is genuinely gone, the gate disarms on its own."
  exit 0
fi

if [ -z "$CURRENT_HASH" ]; then
  exit 0
fi

# Plans exist — look for a review covering this exact plan state.
SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')

# Active-marker bypass: the /plan-review skill is currently running.
# This marker stays strictly session-keyed, unlike the completion marker
# below: it asserts "a review is running in THIS process right now", which is
# a genuinely per-session property, and it is PID-liveness-checked.
# Skip this bypass for ExitPlanMode — the active marker means plan-review
# is in progress (not yet complete), so ExitPlanMode must still be blocked.
# An id that is not a safe single path component (e.g. containing "../") is
# treated the same as absent: ACTIVE_MARKER below concatenates it into a
# path, and skipping the bypass here just means the completion-marker check
# further down decides the gate instead — never less safe than the bypass.
if [ -n "$SESSION_ID" ] && [ "$TOOL_NAME" != "ExitPlanMode" ] && _lib_valid_session_id_component "$SESSION_ID"; then
  ACTIVE_MARKER="$HOME/.claude/.plan-review-active.d/$SESSION_ID"
  if [ -f "$ACTIVE_MARKER" ]; then
    STORED_PID=$(cat "$ACTIVE_MARKER" 2>/dev/null | tr -d '[:space:]')
    if [[ "$STORED_PID" =~ ^[0-9]+$ ]] && kill -0 "$STORED_PID" 2>/dev/null; then
      exit 0
    fi
    rm -f "$ACTIVE_MARKER" 2>/dev/null
  fi
fi

# Completion-marker check: allow only when some marker's stored hash equals
# the active plan set's current hash. An edit to any active plan since the
# marker was written (including a ledger row) changes CURRENT_HASH, so no
# marker matches and the gate re-arms. That content-addressing is precisely
# what makes reading across the filename's session key safe: the hash proves
# which state was reviewed, so the gate asks "has this plan state been
# reviewed?" rather than "did this session review it?".
#
# Two tiers, for latency. This hook fires on every Write/Edit/MultiEdit/
# ExitPlanMode and each repo-hash costs a sha256sum fork, so tier 1 is a
# single hash covering the common case (a resumed session reading its own
# still-valid review). Tier 2 hashes every worktree root and only runs after
# tier 1 misses — i.e. only when the gate is about to deny anyway.
PLAN_REVIEW_MARKERS_DIR="$HOME/.claude/plan-review-markers"
REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
if _lib_marker_value_present "$PLAN_REVIEW_MARKERS_DIR" "$CURRENT_HASH" "$REPO_HASH."; then
  exit 0
fi

# Tier 2 — sibling worktrees of this same repository. The plan hash covers
# repo-RELATIVE paths plus contents, so a plan copied into a fresh worktree
# hashes identically and a review performed in one worktree covers the
# identical plan text in another.
#
# Scoped to `git worktree list` output rather than reading the marker
# directory repo-agnostically: an unrelated repository holding a plan file at
# the same relative path with the same contents would otherwise release this
# gate, having reviewed that text against a different codebase.
#
# What a tier-2 hit authorizes, stated plainly: content-identity of the plan
# text, not state-identity of the sibling's checkout. Two worktrees on
# divergent branches that hold byte-identical plan files cross-validate even
# though neither review assessed the other's HEAD. Bounded to one repository's
# own worktrees, so it is not an external surface, but it is a broader
# acceptance than the copied-plan case alone.
#
# Cost, stated plainly: tier 1 misses for the whole window between authoring a
# plan and its first clean /plan-review, which is the normal state of a session
# actively drafting. So this tier's `git worktree list` fork plus one
# sha256sum per worktree is a per-edit steady-state cost during drafting, not
# an occasional deny-path cost. Worktree count and marker count both grow
# unboundedly and independently, so that cost compounds over a repo's life.
#
# A failed or timed-out enumeration falls through to the deny below. Fewer
# worktrees scanned must never mean "allow" — same fail-closed discipline
# _lib_active_plan_hash applies to its own git calls.
if WORKTREE_LIST=$(_lib_capped git -C "$REPO_ROOT" worktree list --porcelain 2>/dev/null); then
  SIBLING_PREFIXES=()
  while IFS= read -r worktree_line; do
    case "$worktree_line" in
      "worktree "*) ;;
      *) continue ;;
    esac
    SIBLING_ROOT="${worktree_line#worktree }"
    [ -n "$SIBLING_ROOT" ] || continue
    # Tier 1 already scanned this repo's own prefix.
    [ "$SIBLING_ROOT" != "$REPO_ROOT" ] || continue
    SIBLING_PREFIXES+=("$(_marker_lib_repo_hash "$SIBLING_ROOT").")
  done <<< "$WORKTREE_LIST"
  # The count guard is load-bearing under `set -u`: expanding an empty array
  # is an unbound-variable error on bash before 4.4 (stock macOS ships 3.2).
  if [ "${#SIBLING_PREFIXES[@]}" -gt 0 ] \
    && _lib_marker_value_present "$PLAN_REVIEW_MARKERS_DIR" "$CURRENT_HASH" "${SIBLING_PREFIXES[@]}"; then
    exit 0
  fi
fi

# Scope the deny to writes inside this repo. Writes targeting user-home
# directories (~/.claude/plans/), /tmp, or other repos are outside the gate's
# intent — the gate guards this repo's code, not all files on disk.
# ExitPlanMode carries no file_path, so TARGET_PATH is always empty for it;
# this block is skipped and ExitPlanMode reaches emit_deny unconditionally.
if [ -n "$TARGET_PATH" ]; then
  REAL_REPO=$(realpath -m "$REPO_ROOT")
  REAL_TARGET=$(realpath -m "$TARGET_PATH")
  # Reviewer findings writes are exempt — they land in the gitignored
  # agent-reviews/ directory and are never staged. Blocking them forces the
  # reviewer into a full-inline fallback that loses all context savings.
  # Exact prefix match only: "foo-agent-reviews/" does not satisfy this.
  # Note: realpath -m normalizes lexically (resolves ..) but does NOT resolve
  # symlinks. On a repo accessed via a symlinked path, REAL_REPO and REAL_TARGET
  # may be normalized along different chains, causing a false-deny for a
  # legitimate agent-reviews/ write. This is a known limitation shared with
  # the repo-boundary check below; the fix would be `realpath` (requires existence).
  # Portability note: realpath -m is GNU coreutils. On macOS without Homebrew
  # coreutils, realpath -m exits non-zero and (without set -e) silently sets
  # REAL_REPO to empty string, causing both boundary checks to become no-ops.
  # Pre-existing behavior; this exemption check inherits the same characteristic.
  if [[ "$REAL_TARGET" == "$REAL_REPO"/agent-reviews/* ]]; then
    exit 0
  fi
  if [[ "$REAL_TARGET" != "$REAL_REPO/"* ]]; then
    exit 0
  fi
fi

if [ "$TOOL_NAME" = "ExitPlanMode" ]; then
  emit_deny "Plan presentation blocked by the plan-review gate: an uncommitted or modified plan file exists in .claude/plans/ but no plan-review marker covering the current plan set was found.

  Run /plan-review against the plan file before calling ExitPlanMode. The skill records the review in ~/.claude/plan-review-markers/ and plan presentation will be allowed on retry.

  If no plan covers this session yet → run /plan-it first. It authors the plan and hands off to /plan-review."
else
  emit_deny "Write/Edit blocked by plan-review gate: an uncommitted or modified plan file exists in .claude/plans/ but no plan-review marker covering the current plan set was found. A review from an earlier session still counts — the gate matches on the plan's content, not on which session reviewed it — so this means the plan set has changed since its last review, or has never been reviewed. Committed, unmodified plan files are treated as historical and do not arm the gate. Next step depends on whether a plan covers this change:

  - If a plan covers this change → run /plan-review against it. The skill records the review in ~/.claude/plan-review-markers/ and this write will be allowed through on retry.

  - If no plan covers this change yet → run /plan-it first. It authors the plan and hands off to /plan-review at the end.

The model judges which case applies from conversation context. Plans live wherever you put them — typically .claude/plans/, but also /tmp/<slug>.md, handoff docs, or external design doc URLs. The hook does not try to detect plan-change correlation."
fi
