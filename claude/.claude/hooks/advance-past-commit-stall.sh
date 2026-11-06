#!/usr/bin/env bash
# hook-class: turn-gate
# Stop hook: force forward progress past a commit/push/PR permission-question
# stall (GH-526), in repos where the engineer has opted in to autonomous
# shipping. Fires only on a session (never a subagent) whose last message
# asks permission to commit/push/open a PR while work is pending, in a repo
# where _lib_autonomous_shipping_active holds.
#
# Dispatch: matcher-less Stop (per Anthropic's docs, Stop has no matcher
# support). Fail-silent posture throughout — this hook must never emit a
# malformed block payload; every non-firing path is a plain `exit 0`.
# The reason is encoded through the capped _lib_jq wrapper, so a failed
# encode emits nothing and a broken, missing, or hung jq degrades to
# silent-allow, not a stuck block loop.
#
# set -euo pipefail deliberately omitted (nudge-worktree-anchor.sh:51-54
# is the precedent): this hook inspects many non-zero exits (grep/git/jq
# with no match, unset optional fields) as expected control flow, and
# strict mode would turn those into premature exits that skip the
# fail-silent tail.
#
# Kill switches: the commit_stall_block config key (`commit_stall_block = false`
# in <config-dir>/claude-config.toml; the legacy <config-dir>/.commit-stall-block-disabled
# file counts only while the key has no row there) and
# <repo>/.claude/autonomous-shipping-optout (repo-scoped, read via
# _lib_autonomous_shipping_active).
#
# STATE_DIR gets a 30-day eviction sweep on every fire, since state files are
# themselves only ever written here — no SessionEnd hook cleans them up.
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
# - This hook does not gate a git operation, so --dry-run/default-branch
#   bypass shapes from the push-gate arm are irrelevant here.
# - STATE_FILE's write-then-read-back race is untested: assumes Stop fires
#   at most once per session at a time (the harness's own invocation model),
#   not two concurrent processes racing the same session_id/prompt_id.

INPUT=$(cat 2>/dev/null)
[ -z "$INPUT" ] && exit 0

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  exit 0
fi

# 1. The config dir must resolve and be usable before anything below touches it.
CONFIG_DIR=$(_lib_config_dir) || exit 0
[ -d "$CONFIG_DIR" ] || exit 0

# 2. Always-effective kill switch, independent of sentinel state. Delegates
# to _config_enabled's commit_stall_block schema row (presence-disables).
# Only exit code 1 (explicitly disabled) turns this hook off -- every other
# outcome, including config-keys.psv being transiently unreadable (exit 3)
# or readable but missing commit_stall_block's own row (exit 4), leaves it
# armed. See config-schema-audit.md's commit_stall_block section for the
# full fail-direction rationale.
_config_enabled commit_stall_block
case "$?" in
  1) exit 0 ;;
  *) ;;
esac

# 3. Machine-sentinel fast path: the cheap (no parsed input
# needed) half of the full _lib_autonomous_shipping_active check at step 9
# below. This fast path delegates to _lib_autonomous_shipping_sentinel_present,
# which unions the resolved config dir with the legacy ~/.claude location the
# same way step 9's full check does. Absent on the vast majority of
# non-adopting sessions, so checking it here skips spawning jq for the
# common case. The full check (this file plus the per-repo optout) still
# runs at step 9, once REPO_ROOT is known. This is a redundant, cheaper
# pre-filter, not a replacement for it.
_lib_autonomous_shipping_sentinel_present || exit 0

# Six fields in a single jq pass, the same shape as nudge-handoff-near-context-cap.sh's
# multi-field read. Pre-initialized so a failed read leaves empty strings, not
# unbound variables.
# last_assistant_message stays the last field and is read to EOF, because it can span lines.
# It is read with $(cat), because `read -d ''` is expected to read a pipe byte by byte, which was not measured; see docs/design-decisions/autonomous-shipping-state-in-hook-context.md.
# A newline inside a field would shift the later fields of the line-oriented read.
# A .cwd that is not a single-line string is blanked, which step 9 denies.
# Any other header field that is not a single-line string empties every field, which gate 5 denies.
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
  LAST_ASSISTANT_MESSAGE=$(cat)
} < <(
  # shellcheck disable=SC2016 # single-quoted on purpose: $headers and $cwd are jq variable bindings, not shell variables.
  printf '%s\n' "$INPUT" \
    | _lib_jq -r 'def single_line: type == "string" and (contains("\n") | not);
        [(.session_id // ""),(.agent_type // ""),(.permission_mode // ""),(.prompt_id // "")] as $headers
        | ((.cwd // "") | if single_line then . else "" end) as $cwd
        | if ($headers | all(single_line)) then ($headers[], $cwd, (.last_assistant_message // "")) else empty end' \
    2>/dev/null
) 2>/dev/null || true

LOG_FILE="$CONFIG_DIR/.commit-stall-block.log"

# 4. A Stop payload with agent_type set is never force-continued, because
# CLAUDE.md's Agent Core tells any fork or subagent to return its work to its
# dispatcher rather than ship.
# This gate covers a fork only if the fork's Stop payload carries agent_type,
# which is unverified.
# AGENT_TYPE-unreadable and AGENT_TYPE-absent pass this gate as the main
# session does.
# That is safe only because a jq/read failure also empties SESSION_ID, which
# gate 5 below independently denies.
# The safety rests on that ordering, not on an explicit fail-closed check on
# this field itself.
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

# Pattern removal over a long last line is expected to be slow, so only the message's tail is scanned; see docs/design-decisions/autonomous-shipping-state-in-hook-context.md.
# A last sentence longer than the window is judged on its final ASK_WINDOW_CHARS characters.
ASK_WINDOW_CHARS=2048
if [ "${#LAST_ASSISTANT_MESSAGE}" -gt "$ASK_WINDOW_CHARS" ]; then
  LAST_ASSISTANT_MESSAGE="${LAST_ASSISTANT_MESSAGE: -$ASK_WINDOW_CHARS}"
fi
LAST_ASSISTANT_MESSAGE="${LAST_ASSISTANT_MESSAGE%"${LAST_ASSISTANT_MESSAGE##*[![:space:]]}"}"
[ -n "$LAST_ASSISTANT_MESSAGE" ] || exit 0

# 8. Fire predicate on the final sentence: the last sentence of the last non-blank line.
# A quoted example mid-message does not reach this slice.
# No length floor, unlike a tail-byte-count slice.
last_line="${LAST_ASSISTANT_MESSAGE##*$'\n'}"
last_sentence="${last_line##*[.?!][[:space:]]}"

VERB_RE='(want me to|would you like me to|let me know if|shall I|should I|do you want me to)'
OBJECT_RE='(commit|push|open (a|the) PR|create a PR)'
FIRE_RE="${VERB_RE}[^.?!]*${OBJECT_RE}"
EXCLUDE_RE='(merge|--force|force-push|reset --hard|close the PR|delete the branch|failing|failed|error|blocked|anyway)'

# Both regexes MUST stay unquoted in [[ =~ ]] — a quoted pattern is a literal
# string comparison and never matches. Case folding via shopt,
# saved/restored, not grep -i, to stay fork-free.
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

# 9. Repo root + machine-anchored opt-in. The root comes from the payload's cwd
# alone, with no $PWD fallback (see _lib_autonomous_shipping_root_for_cwd);
# the regex above already filtered out most non-firing turns.
REPO_ROOT=$(_lib_autonomous_shipping_root_for_cwd "$CWD") || {
  printf 'root-denied session=%s prompt=%s\n' "$SESSION_ID" "$PROMPT_ID" >> "$LOG_FILE" 2>/dev/null || true
  exit 0
}
SHIPPING_STATEMENT=$(_lib_autonomous_shipping_statement "$REPO_ROOT") || exit 0

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
# Evict stale entries from one-shot runs that skipped SessionEnd cleanup.
if [ -d "$STATE_DIR" ] && [ ! -L "$STATE_DIR" ]; then
  find "$STATE_DIR" -maxdepth 1 -type f -mtime +30 -delete 2>/dev/null || true
fi
printf '%s' "$PROMPT_ID" > "$STATE_FILE" 2>/dev/null || true
WRITTEN_PROMPT_ID=$(cat "$STATE_FILE" 2>/dev/null)
[ "$WRITTEN_PROMPT_ID" = "$PROMPT_ID" ] || exit 0

printf 'fired session=%s prompt=%s\n' "$SESSION_ID" "$PROMPT_ID" >> "$LOG_FILE" 2>/dev/null || true

REASON="$SHIPPING_STATEMENT If the statement above lets you ship this work, continue now: run /code-review, commit with path-scoped staging (never stage-all), run /ready-for-review, and open the PR. If you are genuinely blocked (a failing test you cannot fix, a design ambiguity with no defensible default), say what is blocked instead of asking permission to proceed with work that is already done. To disable this on this machine: printf 'commit_stall_block = false\n' >> \"$CONFIG_DIR/claude-config.toml\". To disable it for this repo: add .claude/autonomous-shipping-optout."

# Mirrors _lib_emit_allow_with_context's gated-encode shape:
# an empty REASON_JSON exits before printing, avoiding the malformed
# `{"decision":"block","reason":}` a bare printf would otherwise emit.
REASON_JSON=$(printf '%s' "$REASON" | _lib_jq -Rs . 2>/dev/null)
[ -z "$REASON_JSON" ] && exit 0
printf '{"decision":"block","reason":%s}\n' "$REASON_JSON" || true
exit 0
