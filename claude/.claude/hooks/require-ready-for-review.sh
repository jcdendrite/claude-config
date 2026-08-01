#!/bin/bash
# hook-class: gate
# Gate: require /ready-for-review to have run before pushing to a branch
# with an open PR (or marking a draft PR ready). Verified via marker file.
#
# Why: pre-handoff gate (/ready-for-review) verifies tests/lint/typecheck,
# runs cumulative /code-review, syncs PR body, and checks CI before the
# human reviews. Without enforcement, Claude habitually pushes wrap-up
# commits straight to a PR without running the gate. Mirrors the
# require-code-review.sh pattern: the marker's content matches the artifact
# about to be published (HEAD SHA, not staged-diff hash — the analogue is
# the squash-merge artifact reviewers see, not the upcoming commit).
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
#   The <session_id> in the filename is a WRITE-side key only: it keeps
#   parallel sessions from overwriting each other's markers. The read globs
#   across it, because the stored HEAD SHA — not the filename — is what
#   proves the gate ran against this exact artifact. Reading the session key
#   as an authorization predicate would deny a resumed session (new
#   session_id) a gate run it already completed at the same HEAD.
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

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by ready-for-review gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

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
    # [[:space:]], not \s: \s is a GNU sed extension. BSD/macOS sed's -E
    # (POSIX ERE) does not support it and silently produces no match at
    # all rather than erroring, which left push_args always empty on
    # macOS -- collapsing every --tags push (branch ref or not) into the
    # tag-only bypass above.
    push_args=$(printf '%s\n' "$COMMAND" | sed -nE 's/.*git[[:space:]]+push[[:space:]]+(.*)/\1/p' | head -1)
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

# Active-marker bypass: the skill is currently running. An absent or
# path-escaping id withholds the bypass, which just means the
# completion-marker check further down decides the gate instead.
if _lib_active_bypass_marker_live ".ready-for-review-active.d" "$SESSION_ID"; then
  exit 0
fi

# PR existence check: only gate when the branch actually has an open PR.
# Network call — wrap in `timeout` so a hanging gh doesn't stall the
# tool-call. On error/timeout, fail-open: the skill's prose triggers
# still fire, and we don't want to brick offline / flaky-network work.
PR_NUMBER=$(cd "$CWD" 2>/dev/null && timeout 5 gh pr view --json number --jq '.number' 2>/dev/null)
if [ -z "$PR_NUMBER" ]; then
  exit 0
fi

# Completion-marker check. Allow when any marker under this repo-hash holds
# the current HEAD SHA: the stored SHA is the authorization, since it names
# the exact artifact the gate ran against. A new commit moves HEAD, so no
# marker matches and the gate re-arms — that content-addressing is what makes
# reading across the filename's session key safe.
# An unresolvable HEAD leaves CURRENT_HEAD empty, which never matches, so a
# failed rev-parse denies rather than releasing the gate.
CURRENT_HEAD=$(cd "$CWD" 2>/dev/null && git rev-parse HEAD 2>/dev/null)
REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
if _lib_marker_value_present "$HOME/.claude/ready-for-review-markers" "$CURRENT_HEAD" "$REPO_HASH."; then
  exit 0
fi

# No active marker, no matching completion marker, gh pr view confirmed
# the branch has an open PR — block.
if $is_gh_pr_ready; then
  emit_deny "PR ready-for-review marking blocked by ready-for-review gate: no /ready-for-review gate run covering the current HEAD was found — either this PR has never been gated, or HEAD has moved since the gate ran. A gate run from an earlier session still counts, so long as HEAD has not moved. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this command will be allowed through. Do not ask the user for permission — run the skill, address any findings, and proceed. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and proceed without re-asking the user."
else
  emit_deny "Push to a branch with an open PR blocked by ready-for-review gate: no /ready-for-review gate run covering this branch's current HEAD was found — either this branch has never been gated, or HEAD has moved since the gate ran. A gate run from an earlier session still counts, so long as HEAD has not moved. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this push will be allowed through. Do not ask the user for permission — run the skill, address any findings, and retry the push. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and push without re-asking the user."
fi
