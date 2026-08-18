#!/bin/bash
# hook-class: informational
# UserPromptSubmit hook: report when a prompt names an installed skill
# (`/<name>`) outside the position the harness auto-expands.
#
# Audience: Claude (the agent), not humans. Output is a JSON payload with
# hookSpecificOutput.additionalContext — the harness injects it into the
# agent's conversation context. Mirrors nudge-worktree-anchor.sh's envelope
# shape.
#
# What this catches: slash-command expansion is anchored to message start,
# and as of v2.1.199 the harness expands a leading run of up to six chained
# skills (code.claude.com/docs/en/commands.md — re-verify there; harness
# versions drift). A skill named later in the prompt is inert text — nothing
# loads unless the agent notices the mention unaided. This hook resolves a
# candidate `/<name>` token outside that leading run against installed skill
# directories and asks the agent to check with the engineer before invoking.
#
# Fail-open, advisory only: every path below exits 0, including a missing
# jq, an unreadable _lib.sh, an unresolvable config dir, and a malformed or
# empty stdin payload. This hook never blocks prompt submission and never
# invokes anything on its own — hooks cannot prompt the user interactively,
# so noticing is deterministic but the agent's follow-up ask is not.
#
# Known gaps, not closed by this hook:
# - Plugin skills (`plugin:skill` form) never resolve — the runtime plugin
#   install path is not derivable here, so this covers personal and project
#   skills only.
# - A mention inside a fenced code block or an inline backtick span is
#   stripped before scanning and never fires, even when it names a real skill.
# - The leading command run (the first token, or a leading chain of up to
#   six) is always skipped, since the harness already expanded it.
# - Unquoted prose that merely discusses a skill without intending to invoke
#   it (e.g. "should I use /plan-it or /brief here?") still fires — an
#   accepted false positive, not a gap this hook tries to close.
# - A single prompt carrying many guessed `/name` tokens is a binary
#   existence oracle over installed skill names, since only the ones that
#   resolve are echoed back; _MAX_CANDIDATE_TOKENS bounds how many one prompt
#   can probe, but does not close the oracle.
#
# No dedup state and no enable/disable sentinel: every qualifying mention
# fires, including a repeat within one prompt or across a conversation, and
# there is no per-machine kill switch short of editing settings.json —
# both deliberate engineer decisions, not oversights.
#
# Prompt text is partly untrusted — pasted issues, logs, and web content
# reach this hook verbatim — so the token grammar is the trust boundary; the
# point-of-use comments below carry it, and docs/hooks.md the summary.

# Strict mode omitted deliberately, matching the other UserPromptSubmit
# nudges: this hook must reach `exit 0` on all paths, and `set -e` would
# turn an expected non-zero (a `grep` with no candidate match, an empty
# PROJECT_SKILLS_ROOT) into an early exit that skips the exit-0 contract.

# Byte cap for the scanned prompt (<100ms per-fire budget, claude-hook-review
# §7), applied right after jq extraction — jq's own parse of the raw payload
# runs uncapped regardless, and a mention past the cap is silently dropped
# since raising it just reopens the stall it prevents.
_MAX_SCAN_BYTES=65536

# Ceiling on distinct candidates per prompt — see "Known gaps" above for the
# existence oracle this bounds.
_MAX_CANDIDATE_TOKENS=32

# Text passes below use sed/awk/grep, not bash %/# trimming — bash's trimming
# is O(n) per call on a capped-but-still-large prompt, so several calls turn
# quadratic fast.

INPUT=$(cat 2>/dev/null)

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# Unresolvable config dir (empty/unset $HOME, no CLAUDE_CONFIG_DIR) stays
# quiet like every other degenerate input this advisory hook tolerates.
CONFIG_DIR=$(_lib_config_dir) || exit 0

# prompt gets its own jq+`head -c` pipeline, never the combined jq pass plus
# bash %%/## split that _lib_parse_tool_input_or_deny uses: that split would
# run on the full untruncated prompt before the byte cap could apply. cwd and
# permission_mode are harness-controlled and small, so the combined pass below
# is safe for them.
PROMPT=$(printf '%s\n' "$INPUT" | jq -r '.prompt // ""' 2>/dev/null | head -c "$_MAX_SCAN_BYTES") || true

CWD=""
PERMISSION_MODE=""
{
  IFS= read -r CWD
  IFS= read -r PERMISSION_MODE
} < <(
  printf '%s\n' "$INPUT" \
    | jq -r '(.cwd // ""),(.permission_mode // "")' \
    2>/dev/null
) 2>/dev/null || true

[ -z "$CWD" ] && CWD="$PWD"

# Nothing to scan: no prompt text, or jq itself failed (missing binary,
# malformed JSON, empty stdin) and left every field empty.
[ -n "$PROMPT" ] || exit 0

# No `/` anywhere means no candidate can exist, so skip the whole pipeline.
# Pure bash, no subprocess — this is the majority case on a per-prompt hook.
case "$PROMPT" in
  */*) ;;
  *) exit 0 ;;
esac

# Every pass below forces LC_ALL=C: under UTF-8, tr/awk/sed/grep abort with no
# stdout on a multi-byte char split by the byte cap above — silently discarding
# the whole prompt, not just the cut bytes — and every pattern here is ASCII so
# byte-wise matching is correct.

# Drop fenced code blocks entirely (opener, body, and closer). `tr` normalizes
# CRLF first so a fence marker is detected regardless of line ending. `/^```/`
# needs no non-greedy operator, so this compiles the same under BSD and GNU
# awk. An unclosed fence drops everything from the opener onward — defined
# behavior.
UNFENCED=$(printf '%s' "$PROMPT" | LC_ALL=C tr -d '\r' \
  | LC_ALL=C awk 'BEGIN{in_fence=0} /^```/{if(in_fence){in_fence=0}else{in_fence=1}; next} !in_fence{print}')

# Drop inline `code spans`. `[^`]*` needs no non-greedy operator, so this
# compiles the same under BSD/macOS sed and GNU sed.
# shellcheck disable=SC2016 # single-quoted on purpose: the backticks are literal characters the sed script matches, not command substitution; double-quoting would make the shell try to execute the span before sed sees it.
UNSPANNED=$(printf '%s' "$UNFENCED" | LC_ALL=C sed -E 's/`[^`]*`//g')

# Skip the leading command run: the first token, or a leading chain of up to
# six `/token` mentions, each separated from the next by whitespace or
# end-of-string. sed for the same reason as the passes above.
SCAN_TEXT=$(printf '%s' "$UNSPANNED" \
  | LC_ALL=C sed -E 's/^[[:space:]]*(\/[A-Za-z0-9_-]+([[:space:]]+|$)){0,6}//')

# Every `/<candidate>` outside the leading run, in first-appearance order.
# The regex itself is the token-grammar boundary: it captures only up to the
# first character outside [A-Za-z0-9_-], so `/`, `..`, and shell
# metacharacters can never enter CANDIDATE_TOKENS in the first place.
declare -a CANDIDATE_TOKENS=()
while IFS= read -r _candidate; do
  [ -n "$_candidate" ] || continue
  CANDIDATE_TOKENS+=("${_candidate#/}")
  [ "${#CANDIDATE_TOKENS[@]}" -ge "$_MAX_CANDIDATE_TOKENS" ] && break
done < <(printf '%s' "$SCAN_TEXT" | LC_ALL=C grep -oE '/[A-Za-z0-9_-]+' 2>/dev/null)

[ "${#CANDIDATE_TOKENS[@]}" -gt 0 ] || exit 0

# Project skills root: walk ancestor directories from CWD looking for
# .claude/skills, trimming one component per level rather than spawning a
# `dirname` per level. No `git` call — this hook never needs the
# main-vs-linked-worktree distinction that costs nudge-worktree-anchor.sh
# its git dependency.
PROJECT_SKILLS_ROOT=""
_walk_dir="$CWD"
# A relative cwd never reaches "/" and `${_walk_dir%/*}` leaves a slash-less
# string unchanged, so the loop below would spin forever on one.
case "$_walk_dir" in
  /*) ;;
  *) _walk_dir="" ;;
esac
while [ -n "$_walk_dir" ]; do
  if [ -d "$_walk_dir/.claude/skills" ]; then
    PROJECT_SKILLS_ROOT="$_walk_dir/.claude/skills"
    break
  fi
  [ "$_walk_dir" = "/" ] && break
  _walk_dir="${_walk_dir%/*}"
  [ -z "$_walk_dir" ] && _walk_dir="/"
done

_token_already_resolved() {
  local needle="$1" item
  for item in "${RESOLVED_SKILLS[@]}"; do
    [ "$item" = "$needle" ] && return 0
  done
  return 1
}

declare -a RESOLVED_SKILLS=()
for _candidate_token in "${CANDIDATE_TOKENS[@]}"; do
  # Point-of-use re-check: a later edit to the capture regex above must not
  # silently widen what reaches a filesystem path.
  [[ "$_candidate_token" =~ ^[A-Za-z0-9_-]+$ ]] || continue
  _token_already_resolved "$_candidate_token" && continue
  if [[ -f "$CONFIG_DIR/skills/$_candidate_token/SKILL.md" ]] \
    || { [ -n "$PROJECT_SKILLS_ROOT" ] && [[ -f "$PROJECT_SKILLS_ROOT/$_candidate_token/SKILL.md" ]]; }; then
    RESOLVED_SKILLS+=("$_candidate_token")
  fi
done

[ "${#RESOLVED_SKILLS[@]}" -gt 0 ] || exit 0

SKILL_LIST=""
for _name in "${RESOLVED_SKILLS[@]}"; do
  if [ -z "$SKILL_LIST" ]; then
    SKILL_LIST="/$_name"
  else
    SKILL_LIST="$SKILL_LIST, /$_name"
  fi
done

ADDITIONAL_CONTEXT=$(printf '%s\n%s' \
  "This prompt mentions ${SKILL_LIST} outside the leading command position, so Claude Code did not expand it as a slash command — nothing loaded automatically." \
  "Ask the engineer whether to invoke it before proceeding: only a mention at the very start of the message, or chained immediately after other leading skill mentions, triggers automatic expansion.")

if [ "$PERMISSION_MODE" = "plan" ]; then
  ADDITIONAL_CONTEXT="$ADDITIONAL_CONTEXT"$'\n'"Since this session is in plan mode: if the engineer confirms, that skill's own workflow governs over the generic plan-mode phases."
fi

jq -n --arg ctx "$ADDITIONAL_CONTEXT" \
  '{hookSpecificOutput: {hookEventName: "UserPromptSubmit", additionalContext: $ctx}}' || true
exit 0
