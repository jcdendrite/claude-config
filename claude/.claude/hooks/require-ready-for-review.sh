#!/bin/bash
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

. "$(dirname "$0")/_lib.sh"

INPUT=$(cat)
TOOL=$(printf '%s\n' "$INPUT" | jq -r '.tool_name // empty')
if [ "$TOOL" != "Bash" ]; then
  exit 0
fi

COMMAND=$(printf '%s\n' "$INPUT" | jq -r '.tool_input.command // empty')
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

# PR existence check: only gate when the branch actually has an open PR.
# Network call — wrap in `timeout` so a hanging gh doesn't stall the
# tool-call. On error/timeout, fail-open: the skill's prose triggers
# still fire, and we don't want to brick offline / flaky-network work.
PR_NUMBER=$(cd "$CWD" 2>/dev/null && timeout 5 gh pr view --json number --jq '.number' 2>/dev/null)
if [ -z "$PR_NUMBER" ]; then
  exit 0
fi

# Completion-marker check.
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

# No active marker, no matching completion marker, gh pr view confirmed
# the branch has an open PR — block.
if $is_gh_pr_ready; then
  REASON="PR ready-for-review marking blocked by ready-for-review gate: this PR has not been gated by /ready-for-review in THIS session, or HEAD has moved since the gate ran. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this command will be allowed through. Do not ask the user for permission — run the skill, address any findings, and proceed. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and proceed without re-asking the user."
else
  REASON="Push to a branch with an open PR blocked by ready-for-review gate: this branch's HEAD has not been gated by /ready-for-review in THIS session, or HEAD has moved since the gate ran. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this push will be allowed through. Do not ask the user for permission — run the skill, address any findings, and retry the push. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and push without re-asking the user."
fi
REASON_JSON=$(printf '%s' "$REASON" | jq -Rs .)
printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}\n' "$REASON_JSON"
