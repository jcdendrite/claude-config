#!/bin/bash
# hook-class: gate
# Gate: require /ready-for-review to have run before pushing to a branch
# with an open PR, marking a draft PR ready, or creating a PR. Verified via
# marker file.
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
# - Not Bash tool, or not git push / gh pr ready / gh pr create.
# - The next three are judged per git-push fragment, so a bypassable push
#   chained ahead of a gated fragment does not exempt it:
#   - --dry-run pushes
#   - --tags-only pushes (no branch artifact change)
#   - Deletion pushes (--delete flag, or `origin :branch` source-empty form)
# - Branch is the default branch (no PR semantics)
# - Branch has no open PR (gh pr view returns empty) — not checked for
#   gh pr create, since a PR being created does not exist yet.
# - gh pr view fails (network issue, gh not configured, etc.) — fail-open
#   to keep the user unblocked; the skill's prose triggers still fire.
#
# Known gaps:
# - The default-branch bypass runs before any command-type check, so
#   `gh pr create` from the default branch is also exempted — believed inert
#   in practice, since gh errors on a same-branch PR regardless of this hook.
# - The gh pr ready and gh pr create arms detect via plain-text regex on the
#   literal `gh pr ready`/`gh pr create` tokens, not the git-push arm's
#   token-walking tokenizer, so a full-path invocation (`/usr/bin/gh pr
#   create`) bypasses detection for those two arms.
# Every git rev-parse/symbolic-ref call in this script is capped via
# _lib_capped, so a stalled filesystem or locked index fails fast (5s)
# instead of hanging indefinitely. REPO_ROOT, CURRENT_BRANCH/DEFAULT_BRANCH,
# and the default-branch candidate loop fail OPEN on that timeout (see
# guard-settings-session-keys.sh's identically named "fail-open posture" for
# precedent). Only CURRENT_HEAD fails closed on that timeout (see its own
# comment below).
#
# Dispatch: wired on the PreToolUse `Bash` matcher with NO `if`-condition —
# intentional, because a prefix glob (`Bash(gh pr create *)`) cannot deliver
# the wrapped, env-prefixed, and `git -C`-style forms the in-script fragment
# tokenizer detects.
# - The hook exits before any git, network, or marker work when no gated
#   command is present.
# - A command that merely mentions a gated command in free text is denied —
#   fail-closed by design.
# - A missing jq denies every Bash call, the posture every unconditional
#   gate in this repo already has.
# - This gate's threat model is cooperative, not adversarial — the same
#   posture require-respond-pr.sh's header states for its own gate.
# - The backstop against deliberate evasion is block-gh-pr-merge.sh blocking
#   self-merge, plus CI rerunning the full suite on push. That backstop
#   holds absent one of block-gh-pr-merge.sh's own documented bypasses:
#   - the `gh api .../pulls/N/merge` endpoint
#   - an `eval`/`bash -c` subshell wrapper
#   - a full-path `gh` invocation
#   It also assumes branch protection requires CI to pass before merge, a
#   GitHub setting this hook cannot itself verify.
# - Unconditional dispatch means _lib_split_fragments et al. now run on
#   every Bash call across every consumer's shell, not only
#   push/pr-create/pr-ready commands, so any latent portability gap in that
#   shared path is now fully exposed, not reached only by a narrow slice of
#   commands.

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

[ -z "$COMMAND" ] && exit 0

SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty')
CWD=$(printf '%s\n' "$INPUT" | _lib_jq -r '.cwd // empty')
[ -z "$CWD" ] && CWD="$PWD"

# True when a `git push` fragment publishes a branch ref a reviewer would see.
# --dry-run pushes nothing.
# --delete/-d removes every listed ref rather than publishing one.
# The colon refspec form (`origin :branch`) removes a ref only when every
# refspec in the fragment is delete-shaped; a real refspec alongside it
# publishes, so this arm also needs the exhaustive-remaining-args check.
# --tags with no other refspec publishes only tags.
push_fragment_publishes_reviewable_change() {
  local fragment="$1"
  local remaining
  if printf '%s\n' "$fragment" | grep -qE '(^|\s)--dry-run(\s|$)'; then
    return 1
  fi
  if printf '%s\n' "$fragment" | grep -qE '(^|\s)(-d|--delete)(\s|$)'; then
    return 1
  fi
  if printf '%s\n' "$fragment" | grep -qE '\s:[A-Za-z0-9._/-]+(\s|$)'; then
    # Delete-only holds only when every refspec is a deletion form, since a
    # real refspec alongside one is reviewable.
    # A literal $( or backtick anywhere in $COMMAND disqualifies the
    # delete-only bypass, since a runtime branch ref can hide inside a
    # substitution that _lib_split_fragments treats as a fragment boundary.
    if printf '%s\n' "$COMMAND" | grep -qE '\$\(|`'; then
      return 0
    fi
    remaining=$(_lib_extract_git_subcmd_args "$fragment" \
      | grep -vE '^(--force(-with-lease)?(=.*)?|--force-if-includes|-u|--set-upstream|origin|upstream|:[A-Za-z0-9._/-]+)$' \
      | grep -v '^$' || true)
    if [[ -z "$remaining" ]]; then
      return 1
    fi
  fi
  if printf '%s\n' "$fragment" | grep -qE '(^|\s)--tags(\s|$)'; then
    # Tag-only holds only when --tags is the sole refspec hint, since a
    # branch ref alongside it is reviewable.
    # A literal $( or backtick anywhere in $COMMAND disqualifies the
    # tags-only bypass, since a runtime branch ref can hide inside a
    # substitution that _lib_split_fragments treats as a fragment boundary.
    if printf '%s\n' "$COMMAND" | grep -qE '\$\(|`'; then
      return 0
    fi
    remaining=$(_lib_extract_git_subcmd_args "$fragment" \
      | grep -vE '^(--tags|--force(-with-lease)?(=.*)?|--force-if-includes|-u|--set-upstream|origin|upstream)$' \
      | grep -v '^$' || true)
    if [[ -z "$remaining" ]]; then
      return 1
    fi
  fi
  return 0
}

# Detect gated commands by tokenizing fragments, not regex. This handles:
# git -C <path> push, git --git-dir=... push, GIT_DIR=... git push,
# eval git push, xargs git push, git push; (trailing semicolon),
# (cd /wt; git push) (paren group). A push fragment that publishes nothing
# reviewable is not a gated command.
is_gated_git_push=false
is_gh_pr_ready=false
is_gh_pr_create=false
FRAGMENTS=$(_lib_split_fragments "$COMMAND")
while IFS= read -r frag; do
  [ -z "$frag" ] && continue
  if _lib_fragment_invokes_git "$frag"; then
    subcmd=$(_lib_extract_git_subcmd "$frag")
    if [[ "$subcmd" == "push" ]] && push_fragment_publishes_reviewable_change "$frag"; then
      is_gated_git_push=true
    fi
  fi
  if printf '%s\n' "$frag" | grep -qE '(^|\s)gh\s+pr\s+ready(\s|;|$)'; then
    is_gh_pr_ready=true
  fi
  if printf '%s\n' "$frag" | grep -qE '(^|\s)gh\s+pr\s+create(\s|;|$)'; then
    is_gh_pr_create=true
  fi
done <<< "$FRAGMENTS"
if ! $is_gated_git_push && ! $is_gh_pr_ready && ! $is_gh_pr_create; then
  exit 0
fi

# Are we in a git repo?
REPO_ROOT=$(cd "$CWD" 2>/dev/null && _lib_capped git rev-parse --show-toplevel 2>/dev/null)
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
CURRENT_BRANCH=$(cd "$CWD" 2>/dev/null && _lib_capped git rev-parse --abbrev-ref HEAD 2>/dev/null)
DEFAULT_BRANCH=$(cd "$CWD" 2>/dev/null && _lib_capped git symbolic-ref --quiet refs/remotes/origin/HEAD 2>/dev/null | sed 's|^refs/remotes/origin/||')
if [ -z "$DEFAULT_BRANCH" ]; then
  for candidate in main master develop; do
    if cd "$CWD" 2>/dev/null && _lib_capped git rev-parse --verify "origin/$candidate" >/dev/null 2>&1; then
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
# Skipped for gh pr create — a PR being created by definition does not
# exist yet, so this early-return would otherwise fail-open the one
# command it needs to gate.
# Network call — wrap in `timeout` so a hanging gh doesn't stall the
# tool-call. On error/timeout, fail-open: the skill's prose triggers
# still fire, and we don't want to brick offline / flaky-network work.
if ! $is_gh_pr_create; then
  PR_NUMBER=$(cd "$CWD" 2>/dev/null && timeout 5 gh pr view --json number --jq '.number' 2>/dev/null)
  if [ -z "$PR_NUMBER" ]; then
    exit 0
  fi
fi

# Completion-marker check. Allow when any marker under this repo-hash holds
# the current HEAD SHA: the stored SHA is the authorization, since it names
# the exact artifact the gate ran against. A new commit moves HEAD, so no
# marker matches and the gate re-arms — that content-addressing is what makes
# reading across the filename's session key safe.
# An unresolvable HEAD leaves CURRENT_HEAD empty, which never matches, so a
# failed rev-parse denies rather than releasing the gate.
CURRENT_HEAD=$(cd "$CWD" 2>/dev/null && _lib_capped git rev-parse HEAD 2>/dev/null)
REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
# Fail closed: an unresolvable config dir must deny the gate, not silently
# skip the marker check and let the push/PR-ready command through.
if ! CONFIG_DIR=$(_lib_config_dir); then
  emit_deny "Blocked by ready-for-review gate: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty)."
  exit 0
fi
if _lib_marker_value_present "$CONFIG_DIR/ready-for-review-markers" "$CURRENT_HEAD" "$REPO_HASH."; then
  exit 0
fi

# No active marker, no matching completion marker, and (for git push /
# gh pr ready) gh pr view confirmed the branch has an open PR — block,
# naming whichever of the three commands triggered the gate.
if $is_gh_pr_ready; then
  emit_deny "PR ready-for-review marking blocked by ready-for-review gate: no /ready-for-review gate run covering the current HEAD was found — either this PR has never been gated, or HEAD has moved since the gate ran. A gate run from an earlier session still counts, so long as HEAD has not moved. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this command will be allowed through. Do not ask the user for permission — run the skill, address any findings, and proceed. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and proceed without re-asking the user."
elif $is_gh_pr_create; then
  emit_deny "PR creation blocked by ready-for-review gate: no /ready-for-review gate run covering the current HEAD was found. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this command will be allowed through. Do not ask the user for permission — run the skill, address any findings, and proceed. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and proceed without re-asking the user."
else
  emit_deny "Push to a branch with an open PR blocked by ready-for-review gate: no /ready-for-review gate run covering this branch's current HEAD was found — either this branch has never been gated, or HEAD has moved since the gate ran. A gate run from an earlier session still counts, so long as HEAD has not moved. Run the /ready-for-review skill now — it verifies tests/lint/typecheck, runs cumulative /code-review against the PR-vs-default-branch diff, syncs the PR body, and checks CI. When all halt-on-fail steps pass, the skill records completion in ~/.claude/ready-for-review-markers/ and this push will be allowed through. Do not ask the user for permission — run the skill, address any findings, and retry the push. If HEAD moved because /code-review iteration produced fix commits this session, those commits are inside the approved scope of the gate; re-run and push without re-asking the user."
fi
