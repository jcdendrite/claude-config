#!/bin/bash
# hook-class: turn-gate
# Stop hook: force forward progress past a commit/push/PR permission-question
# stall (GH-526), in repos where the engineer has opted in to autonomous
# shipping. Fires only on a session (never a subagent) whose last message
# asks permission to commit/push/open a PR while work is pending, in a repo
# where _lib_autonomous_shipping_active holds.
#
# Dispatch: matcher-less Stop (per Anthropic's docs, Stop has no matcher
# support). Fail-silent posture throughout — this hook must never emit a
# malformed block payload; every non-firing path is a plain `exit 0`, and
# the emitting jq call itself has an `|| true` plus an explicit trailing
# `exit 0` so a broken/missing jq degrades to silent-allow, not a stuck
# block loop.
#
# set -euo pipefail deliberately omitted (nudge-worktree-anchor.sh:51-54
# is the precedent): this hook inspects many non-zero exits (grep/git/jq
# with no match, unset optional fields) as expected control flow, and
# strict mode would turn those into premature exits that skip the
# fail-silent tail.
#
# Kill switches: ~/.claude/.commit-stall-block-disabled (always-effective,
# regardless of sentinel state) and <repo>/.claude/autonomous-shipping-optout
# (repo-scoped, read via _lib_autonomous_shipping_active).
#
# Known gaps (see docs/commit-stall-block.md for the full design record):
# - Exclusion-window is scoped to the final sentence, same as the fire
#   window, not the whole message: a failure signal in an earlier sentence
#   ("The push failed... Want me to push again?") is missed and this hook
#   fires once, forcing one wasted retry — accepted tradeoff, see the plan.
# - The predicate requires a question construction; a silent (non-question)
#   stall is not caught here.
# - _lib_capped's timeout(1) fallback is a no-op on stock macOS without GNU
#   coreutils, so the three git calls below are unbounded there.
# - --dry-run/default-branch bypass residuals inherited from Part 3's gate
#   repair are orthogonal to this hook (it does not gate a git operation).
# - STATE_FILE's write-then-read-back race is untested: assumes Stop fires
#   at most once per session at a time (the harness's own invocation model),
#   not two concurrent processes racing the same session_id/prompt_id.

INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# 1. The config dir must resolve and be usable before anything below touches it.
CONFIG_DIR=$(_lib_config_dir) || exit 0
[ -d "$CONFIG_DIR" ] || exit 0

# 2. Always-effective kill switch, independent of sentinel state.
[ -f "$CONFIG_DIR/.commit-stall-block-disabled" ] && exit 0

# 3. Machine-sentinel fast path: the cheap (bare stat, no parsed input
# needed) half of the full _lib_autonomous_shipping_active check at step 9
# below. Absent on the vast majority of non-adopting sessions, so checking
# it here skips spawning jq for the common case. The full check (this file
# plus the per-repo optout) still runs at step 9, once REPO_ROOT is known —
# this is a redundant, cheaper pre-filter, not a replacement for it.
[ -f "$CONFIG_DIR/autonomous-shipping-required" ] || exit 0

# Six fields in a single jq pass (nudge-handoff-near-context-cap.sh:29-49
# pattern). Pre-initialized so a failed read leaves empty strings, not
# unbound variables.
SESSION_ID=""
AGENT_TYPE=""
PERMISSION_MODE=""
PROMPT_ID=""
CWD=""
LAST_ASSISTANT_MESSAGE=""
{
  IFS= read -r SESSION_ID
  IFS= read -r AGENT_TYPE
  IFS= read -r PERMISSION_MODE
  IFS= read -r PROMPT_ID
  IFS= read -r CWD
  IFS= read -r LAST_ASSISTANT_MESSAGE
} < <(
  printf '%s\n' "$INPUT" \
    | _lib_jq -r '(.session_id // ""),(.agent_type // ""),(.permission_mode // ""),(.prompt_id // ""),(.cwd // ""),(.last_assistant_message // "")' \
    2>/dev/null
) 2>/dev/null || true

LOG_FILE="$CONFIG_DIR/.commit-stall-block.log"

# 4. Subagents are never force-continued — only the session the engineer is
# talking to (CLAUDE.md's Shipping section states this explicitly).
# AGENT_TYPE-unreadable and AGENT_TYPE-absent take this same branch; that's
# safe only because a jq/read failure also empties SESSION_ID, which gate 4
# below independently denies — load-bearing on that ordering, not an
# explicit fail-closed check on this field itself.
[ -z "$AGENT_TYPE" ] || exit 0

# 5. session_id required and must be a safe single path component; it feeds
# STATE_FILE below.
[ -n "$SESSION_ID" ] && _lib_valid_session_id_component "$SESSION_ID" || exit 0

# 6. Never force-continue out of plan mode.
[ "$PERMISSION_MODE" != "plan" ] || exit 0

# 7. prompt_id required (guards an empty-vs-absent-state-file comparison
# ambiguity) and must differ from the last-fired prompt_id for this session
# — at most one forced continuation per user turn, re-arming on a new turn.
if [ -z "$PROMPT_ID" ]; then
  printf 'schema-drift session=%s field=prompt_id\n' "$SESSION_ID" >> "$LOG_FILE" 2>/dev/null || true
  exit 0
fi

STATE_DIR="$CONFIG_DIR/.commit-stall-block.d"
STATE_FILE="${STATE_DIR}/${SESSION_ID}"
PREVIOUSLY_FIRED_PROMPT_ID=$(cat "$STATE_FILE" 2>/dev/null)
[ "$PREVIOUSLY_FIRED_PROMPT_ID" = "$PROMPT_ID" ] && exit 0

[ -n "$LAST_ASSISTANT_MESSAGE" ] || exit 0

# 8. Fire predicate, final sentence only. Split on ". " / "? " / "! "
# followed by a capital or end-of-string, take the last segment — a quoted
# example mid-message does not reach this slice. No length floor (unlike a
# tail-byte-count slice, which returns empty under ~600 chars on bash 3.2 —
# see docs/commit-stall-block.md for the empirically-confirmed defect this
# replaced).
last_sentence="${LAST_ASSISTANT_MESSAGE##*[.?!] }"

VERB_RE='(want me to|would you like me to|let me know if|shall I|should I|do you want me to)'
OBJECT_RE='(commit|push|open (a|the) PR|create a PR)'
FIRE_RE="${VERB_RE}[^.?!]*${OBJECT_RE}"
EXCLUDE_RE='(merge|--force|force-push|reset --hard|close the PR|delete the branch|failing|failed|error|blocked|anyway)'

# Both regexes MUST stay unquoted in [[ =~ ]] — a quoted pattern falls back
# to literal string comparison on bash 3.2.57 and never matches (verified
# directly on this machine). Case folding via shopt, saved/restored, not
# grep -i, to stay fork-free.
_SAVED_NOCASEMATCH=$(shopt -p nocasematch)
shopt -s nocasematch
FIRE_MATCHED=false
EXCLUDE_MATCHED=false
VERB_ONLY_MATCHED=false
if [[ $last_sentence =~ $FIRE_RE ]]; then
  FIRE_MATCHED=true
elif [[ $last_sentence =~ $VERB_RE ]]; then
  # Near-miss: permission-verb half matched, object half did not. Logged
  # (not fired on) so a future model rewording the stall is visible without
  # logging on nearly every non-matching turn.
  VERB_ONLY_MATCHED=true
fi
if [[ $last_sentence =~ $EXCLUDE_RE ]]; then
  EXCLUDE_MATCHED=true
fi
eval "$_SAVED_NOCASEMATCH"

if $VERB_ONLY_MATCHED; then
  printf 'phrasing-drift session=%s prompt=%s\n' "$SESSION_ID" "$PROMPT_ID" >> "$LOG_FILE" 2>/dev/null || true
fi

if ! $FIRE_MATCHED || $EXCLUDE_MATCHED; then
  exit 0
fi

# 9. Repo root + machine-anchored opt-in. One git spawn; regex already
# filtered out most non-firing turns above.
[ -z "$CWD" ] && CWD="$PWD"
REPO_ROOT=$(cd "$CWD" 2>/dev/null && git rev-parse --show-toplevel 2>/dev/null)
[ -n "$REPO_ROOT" ] || exit 0
_lib_autonomous_shipping_active "$REPO_ROOT" || exit 0

# 10. Work pending: dirty tree, HEAD ahead of its configured upstream, or —
# when no upstream is configured at all, the common state of a branch
# before its first push — any resolvable HEAD (nothing has ever been
# published, so a commit existing at all is unpublished work). Deliberately
# NOT "commits not on origin/<default>" as the general case — that stays
# true for a branch's whole life including after the PR opens, which would
# re-fire forever; the no-upstream fallback below is narrower and only
# reachable in the transient pre-first-push window, since the first
# `git push -u` (ready-for-review/SKILL.md's own push step) configures
# tracking and moves subsequent turns onto the ahead-of-upstream branch.
# Up to three git calls; a failure or hang on any of them yields empty/
# nonzero, read as "no work pending" — fail-safe direction, since
# _lib_capped is not a bound on stock macOS without GNU coreutils
# (_lib.sh:29-35).
_commit_stall_work_pending() {
  local repo_root="$1"
  local dirty ahead
  dirty=$(_lib_capped git -C "$repo_root" status --porcelain 2>/dev/null)
  [ -n "$dirty" ] && return 0
  if ahead=$(_lib_capped git -C "$repo_root" rev-list --count '@{u}..HEAD' 2>/dev/null) && [ -n "$ahead" ]; then
    [ "$ahead" -gt 0 ] 2>/dev/null
    return
  fi
  _lib_capped git -C "$repo_root" rev-parse --verify -q HEAD >/dev/null 2>&1
}

_commit_stall_work_pending "$REPO_ROOT" || exit 0

# Fire. Write state, read it back, and emit block only if it holds the
# current prompt_id — a write failure (read-only $HOME, full disk) must not
# block, so the read-back is the actual gate on emitting.
mkdir -p "$STATE_DIR" 2>/dev/null || true
printf '%s' "$PROMPT_ID" > "$STATE_FILE" 2>/dev/null || true
WRITTEN_PROMPT_ID=$(cat "$STATE_FILE" 2>/dev/null)
[ "$WRITTEN_PROMPT_ID" = "$PROMPT_ID" ] || exit 0

printf 'fired session=%s prompt=%s\n' "$SESSION_ID" "$PROMPT_ID" >> "$LOG_FILE" 2>/dev/null || true

REASON="Per this repo's Shipping policy (CLAUDE.md), autonomous shipping is active — do not stop to ask permission for a commit, push, or PR that is already authorized. Continue: run /code-review, commit with path-scoped staging (never stage-all), run /ready-for-review, and open the PR. Stop before merge — that stays human-only. If you are genuinely blocked (a failing test you cannot fix, a design ambiguity with no defensible default), say what is blocked instead of asking permission to proceed with work that is already done. To disable this for the rest of this session: touch ~/.claude/.commit-stall-block-disabled. To disable it for this repo: add .claude/autonomous-shipping-optout."

jq -n --arg reason "$REASON" '{"decision":"block","reason":$reason}' 2>/dev/null || true
exit 0
