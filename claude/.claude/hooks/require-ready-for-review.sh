#!/bin/bash
# hook-class: gate
# Gate: require /ready-for-review to have run before pushing to a branch
# with an open PR (or marking a draft PR ready). Verified via marker file.
#
# Why: pre-handoff gate (/ready-for-review) verifies tests/lint/typecheck,
# runs cumulative /code-review, syncs PR body, and checks CI before the
# human reviews. Without enforcement, Claude habitually pushes wrap-up
# commits straight to a PR without running the gate. Mirrors the
# require-code-review.sh pattern: marker keyed by session, content matches
# the artifact about to be published (HEAD SHA, not staged-diff hash —
# the analogue is the squash-merge artifact reviewers see, not the
# upcoming commit).
#
# Two-tier gate: pushing to an open DRAFT PR is satisfied by EITHER the
# full ready-for-review marker below OR a lighter sync-pr-description
# completion marker whose recorded SHA matches current HEAD — iteration
# pushes to a draft PR don't need the full gate on every push. `gh pr
# ready` and pushes to a READY (non-draft) PR always demand the full
# marker: the draft→ready transition is the true final-push checkpoint,
# so the draft tier is scoped to the git-push path, not to draft status
# alone. Draft status comes from the same `gh pr view` call already made
# for PR-existence — extended to request both `number,isDraft` in one
# network round-trip.
#
# Two-marker pattern:
# - Active marker (~/.claude/.ready-for-review-active.d/<session_id>):
#   content = Claude session PID. Written by /ready-for-review at step 0;
#   removed at step 7 (completion). Bypasses the gate so the skill's own
#   iteration pushes (step 3 fix → push → loop) don't self-deny. The hook
#   checks PID liveness (kill -0) on each gate hit; dead PIDs are evicted
#   automatically, which handles orphaned markers from sessions that errored
#   before cleanup.
# - Completion marker (~/.claude/ready-for-review-markers/<repo-hash>.<session_id>):
#   contents are the local HEAD SHA at gate-completion time. Written at
#   step 7 only when every halt-on-fail step passed. Pushes against the
#   recorded HEAD are allowed; new commits invalidate the marker
#   automatically (HEAD moves) and force a re-run.
# - Draft-tier completion marker (~/.claude/sync-pr-description-markers/<repo-hash>.<session_id>):
#   same layout and HEAD-SHA mechanics as the completion marker above,
#   written by the sync-pr-description skill. Only consulted on the
#   git-push path when the PR is a draft.
#
# Bypass cases (allow without checking marker):
# - Not Bash tool, or not git push / gh pr ready / gh pr review --approve etc.
# - --dry-run pushes
# - --tags-only pushes (no branch artifact change)
# - Deletion pushes (--delete flag, or `origin :branch` source-empty form)
# - Branch is the default branch (no PR semantics)
# - Branch has no open PR (gh pr view returns empty)
# - gh pr view fails (network issue, gh not configured, etc.) — fail-open
#   to keep the user unblocked; the skill's prose triggers still fire.

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
  emit_deny "Blocked by ready-for-review gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked by ready-for-review gate: could not parse tool-input JSON."

if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

SESSION_ID=$(printf '%s\n' "$INPUT" | jq -r '.session_id // empty')
CWD=$(printf '%s\n' "$INPUT" | jq -r '.cwd // empty')
[ -z "$CWD" ] && CWD="$PWD"

# Detect gated commands by tokenizing fragments, not regex. This handles:
# git -C <path> push, git --git-dir=... push, GIT_DIR=... git push,
# eval git push, xargs git push, git push; (trailing semicolon),
# (cd /wt; git push) (paren group).
is_git_push=false
is_gh_pr_ready=false
FRAGMENTS=$(_lib_split_fragments "$COMMAND")
while IFS= read -r frag; do
  [ -z "$frag" ] && continue
  if _lib_fragment_invokes_git "$frag"; then
    subcmd=$(_lib_extract_git_subcmd "$frag")
    [ "$subcmd" = "push" ] && is_git_push=true
  fi
  if printf '%s\n' "$frag" | grep -qE '(^|\s)gh\s+pr\s+ready(\s|;|$)'; then
    is_gh_pr_ready=true
  fi
done <<< "$FRAGMENTS"
if ! $is_git_push && ! $is_gh_pr_ready; then
  exit 0
fi

# git push bypass shapes — none of these publish a reviewable artifact change.
if $is_git_push; then
  # --dry-run: doesn't actually push.
  if printf '%s\n' "$COMMAND" | grep -qE '(^|\s)--dry-run(\s|$)'; then
    exit 0
  fi
  # --delete or refspec source-empty (`origin :branch`): branch deletion.
  if printf '%s\n' "$COMMAND" | grep -qE '(^|\s)(-d|--delete)(\s|$)'; then
    exit 0
  fi
  if printf '%s\n' "$COMMAND" | grep -qE '\s:[A-Za-z0-9._/-]+(\s|$)'; then
    exit 0
  fi
  # --tags with no explicit refspec other than tags: tag-only push.
  # Conservative: only bypass when --tags is the only refspec hint. If the
  # command also mentions a branch refspec, fall through to the gate.
  if printf '%s\n' "$COMMAND" | grep -qE '(^|\s)--tags(\s|$)'; then
    # If the only non-flag args after `git push` are `--tags` (and possibly
    # a remote name), bypass. If a branch ref is also present, gate.
    push_args=$(printf '%s\n' "$COMMAND" | sed -nE 's/.*git\s+push\s+(.*)/\1/p' | head -1)
    # Strip flags and known-safe positional (a remote like "origin").
    # If anything else remains, it's likely a branch ref → gate.
    remaining=$(printf '%s\n' "$push_args" | tr ' ' '\n' | grep -vE '^(--tags|--force(-with-lease)?(=.*)?|--force-if-includes|-u|--set-upstream|origin|upstream)$' | grep -v '^$' || true)
    if [ -z "$remaining" ]; then
      exit 0
    fi
  fi
fi

# Are we in a git repo?
REPO_ROOT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
if [ -z "$REPO_ROOT" ]; then
  exit 0
fi

# Default-branch bypass: pushing main/master/develop has no PR review
# semantics. Resolve the local default via the symbolic ref refs/remotes/
# origin/HEAD; fall back to a small set of conventional names when origin/
# HEAD isn't configured. Use `git symbolic-ref --quiet` rather than
# `rev-parse --abbrev-ref origin/HEAD`: the latter outputs the literal
# string "origin/HEAD" (not empty) when origin/HEAD isn't a symbolic ref,
# which defeats the fallback path.
CURRENT_BRANCH=$(cd "$CWD" 2>/dev/null && git rev-parse --abbrev-ref HEAD 2>/dev/null)
DEFAULT_BRANCH=$(cd "$CWD" 2>/dev/null && git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's|^refs/remotes/origin/||')
if [ -z "$DEFAULT_BRANCH" ]; then
  for candidate in main master develop; do
    if cd "$CWD" 2>/dev/null && git rev-parse --verify "origin/$candidate" >/dev/null 2>&1; then
      DEFAULT_BRANCH="$candidate"
      break
    fi
  done
fi
if [ -n "$CURRENT_BRANCH" ] && [ -n "$DEFAULT_BRANCH" ] && [ "$CURRENT_BRANCH" = "$DEFAULT_BRANCH" ]; then
  exit 0
fi

# Active-marker bypass: the skill is currently running.
if [ -n "$SESSION_ID" ]; then
  ACTIVE_MARKER="$HOME/.claude/.ready-for-review-active.d/$SESSION_ID"
  if [ -f "$ACTIVE_MARKER" ]; then
    STORED_PID=$(cat "$ACTIVE_MARKER" 2>/dev/null | tr -d '[:space:]')
    if [[ "$STORED_PID" =~ ^[0-9]+$ ]] && kill -0 "$STORED_PID" 2>/dev/null; then
      exit 0
    fi
    rm -f "$ACTIVE_MARKER" 2>/dev/null
  fi
fi

# PR existence + draft-status check: only gate when the branch actually has
# an open PR. Both fields come out of ONE gh call, delimited by an ASCII
# Unit Separator (0x1f) between the two interpolated fields — the same
# technique _lib_parse_tool_input_or_deny uses to extract TOOL_NAME/COMMAND
# together — so the two-tier gate below doesn't cost a second network
# round-trip.
# WARNING: the --jq filter below contains a literal 0x1f (ASCII Unit
# Separator) byte between the two interpolated fields - invisible in
# editors and diff views. Do not remove it.
# Network call — wrap in `timeout` so a hanging gh doesn't stall the
# tool-call. On error/timeout, fail-open: the skill's prose triggers
# still fire, and we don't want to brick offline / flaky-network work.
PR_INFO=$(cd "$CWD" 2>/dev/null && timeout 5 gh pr view --json number,isDraft --jq '"\(.number)\(.isDraft)"' 2>/dev/null)
if [ -z "$PR_INFO" ]; then
  exit 0
fi
IS_DRAFT="${PR_INFO#*$'\x1f'}"

# Completion-marker check (full gate).
if [ -n "$SESSION_ID" ]; then
  REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
  MARKER="$HOME/.claude/ready-for-review-markers/$REPO_HASH.$SESSION_ID"
  if [ -f "$MARKER" ]; then
    MARKER_HEAD=$(tr -d '[:space:]' < "$MARKER")
    CURRENT_HEAD=$(cd "$CWD" 2>/dev/null && git rev-parse HEAD 2>/dev/null)
    if [ -n "$MARKER_HEAD" ] && [ -n "$CURRENT_HEAD" ] && [ "$MARKER_HEAD" = "$CURRENT_HEAD" ]; then
      exit 0
    fi
  fi
fi

# Draft-tier completion-marker check (git push only). A push to an open
# DRAFT PR is also satisfied by the lighter sync-pr-description marker,
# HEAD-keyed the same way as the full marker above. Scoped to is_git_push,
# not to IS_DRAFT alone — gh pr ready is by definition called on a
# still-draft PR and must keep demanding the full ready-for-review marker,
# since the draft→ready transition is the true final-push checkpoint.
if $is_git_push && [ "$IS_DRAFT" = "true" ] && [ -n "$SESSION_ID" ]; then
  REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
  SYNC_MARKER="$HOME/.claude/sync-pr-description-markers/$REPO_HASH.$SESSION_ID"
  if [ -f "$SYNC_MARKER" ]; then
    SYNC_MARKER_HEAD=$(tr -d '[:space:]' < "$SYNC_MARKER")
    CURRENT_HEAD=$(cd "$CWD" 2>/dev/null && git rev-parse HEAD 2>/dev/null)
    if [ -n "$SYNC_MARKER_HEAD" ] && [ -n "$CURRENT_HEAD" ] && [ "$SYNC_MARKER_HEAD" = "$CURRENT_HEAD" ]; then
      exit 0
    fi
  fi
fi

# No active marker, no matching completion marker (and, for draft-PR
# pushes, no matching sync-pr-description marker) — gh pr view confirmed
# the branch has an open PR — block.
if $is_gh_pr_ready; then
  emit_deny "PR ready-for-review marking blocked by ready-for-review gate: this PR has not been gated by /ready-for-review in THIS session, or HEAD has moved since the gate ran. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this command will be allowed through. Do not ask the user for permission — run the skill, address any findings, and proceed. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and proceed without re-asking the user."
elif $is_git_push && [ "$IS_DRAFT" = "true" ]; then
  emit_deny "Push to a draft PR blocked by ready-for-review gate: this branch's HEAD has not been gated by /sync-pr-description or /ready-for-review in THIS session, or HEAD has moved since either ran. Run /sync-pr-description now for a lightweight sync of the PR description (sufficient for iteration pushes to a draft PR) or /ready-for-review for the full gate (verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, checks CI) — either records completion and this push will be allowed through. Do not ask the user for permission — run one of the two skills, address any findings, and retry the push. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and push without re-asking the user."
else
  emit_deny "Push to a branch with an open PR blocked by ready-for-review gate: this branch's HEAD has not been gated by /ready-for-review in THIS session, or HEAD has moved since the gate ran. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this push will be allowed through. Do not ask the user for permission — run the skill, address any findings, and retry the push. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and push without re-asking the user."
fi
