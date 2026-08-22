#!/bin/bash
# hook-class: batch-gate
# PostToolBatch and Stop hook: injects a context-window nudge when the
# estimated token count crosses the lesser of 40% of the model's context
# window or an absolute-token cap (HANDOFF_NUDGE_ABS_CAP, default 360000).
# See docs/handoff-nudge.md "What the hook does" for the dual-registration
# rationale and why cost tracks absolute tokens, not window percentage.
#
# Nudge re-arms at escalating token bands past the first fire — see
# docs/handoff-nudge.md "Why this spacing". Past HANDOFF_NUDGE_BLOCK_AFTER
# ignored re-arms, a further re-arm hard-blocks instead of advising — see
# "Why this block-after count" in the same doc. The hard block uses
# PostToolBatch's own exit-2 loop-stop, not a JSON envelope, and only fires
# on that event: exit 2 on Stop forces continuation instead of blocking it,
# so a Stop-registered re-arm falls through to the advisory path instead.
#
# Kill-switch: touching ~/.claude/.handoff-nudge-disabled suppresses all
# nudges globally, including the hard block above (useful when running
# automated pipelines).
#
# Fail-open everywhere except the escalation block above: any unexpected
# error (missing jq, missing _lib.sh, malformed input) exits 0 with no
# stdout, never a block — the block path only ever triggers from a real,
# previously-recorded fire history, never as a side effect of a broken
# dependency.
#
# Log file: ~/.claude/.handoff-nudge.log records two event types:
#   nudged  session=<id> est=<n> model=<id> window=<n> event=<PostToolBatch|Stop> [action=block]  — threshold crossed, nudge emitted; action=block present only on a hard-block fire
#   schema-drift session=<id> event=<PostToolBatch|Stop>     — usage block present but all token fields 0/null
#
# --check mode: `nudge-handoff-near-context-cap.sh --check` reports the
# session's current estimate, threshold, model, and window as one JSON object
# and writes nothing — no marker, no log line.
# It is invoked by hand (from plan-it Step 7 and the handoff skill), never
# registered in settings.json, so its JSON shape is never emitted on a
# hook-fired path and the fail-open contract above is unchanged. It always
# computes a fresh number via read_latest_usage's own bounded scan, never the
# fire path's incremental-scan cache below.
# It resolves its own session by walking process ancestors, since the harness
# supplies session_id and transcript_path on stdin to hooks only.
# It reports the kill-switch as a field rather than honouring it: the switch
# suppresses notifying, not measuring.
# Refusing is a first-class outcome — every unresolved condition returns
# status "cannot-resolve" with a reason rather than a guessed number.
# See docs/handoff-nudge.md for the JSON contract and the reason vocabulary.
#
# strict mode omitted deliberately: this hook must never block a tool-call
# batch for the wrong reason (only the escalation ladder above may exit 2,
# and only from its own explicit check); strict mode could cause unexpected
# early exits from the || true guards that protect against unwritable dirs
# and missing executables. The --check path inherits that posture and exits
# 0 on every branch too.
#
# Known limitations:
#   - claude -p one-shot runs do not fire SessionEnd, so nudge-fired markers from
#     those sessions accumulate without being cleaned up. Files hold the
#     triggering estimate from first fire onward, not zero bytes.
#   - Model→window resolution below is a hardcoded, dated table — see docs/handoff-nudge.md.
#   - An unrecognized model ID defaults to the 1M window, whose effective threshold
#     is bounded by HANDOFF_NUDGE_ABS_CAP; a future smaller-window model can still
#     silently miss firing if its real window sits below the cap, with no log
#     signal at all.
#   - The fire path's ESTIMATE can lag by one incremental scan: when an
#     appended transcript slice carries no new usage block, the cached
#     estimate from the last fire that did find one is reused rather than
#     re-scanned — accurate as of that fire, not a live count.
#   - --check's number is advisory: its PID-reuse guard compares whole-second
#     start times and it does not re-verify the process identity of the entry
#     it resolves — see docs/handoff-nudge.md.

# --check is detected before the stdin read below, which is unconditional:
# invoked from a Bash tool call with no redirect, that `cat` reads inherited
# stdin and can block.
CHECK_MODE=0
if [ "${1:-}" = "--check" ]; then
  CHECK_MODE=1
fi

# Sourced ahead of the stdin read so both paths can use it; sourcing has no
# side effects, so this costs the fire path only a small, fixed amount of work.
if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  exit 0
fi

# An unresolvable config dir leaves no kill-switch/log/marker location to
# check or write. Captured rather than exited on, so each path below can fail
# in its own idiom: the fire path exits 0, --check refuses with a reason.
CONFIG_DIR=$(_lib_config_dir) || CONFIG_DIR=""

# Context window in tokens per model ID; the pct arm below is 40% of it.
# Source: https://platform.claude.com/docs/en/about-claude/models/overview,
# fetched 2026-08-03; re-verify by 2026-11-03.
# Verified 200k: Haiku 4.5, Sonnet 4.5, Opus 4.5, Opus 4.1. Verified 1M:
# Fable 5, Mythos 5, Opus 5, Opus 4.8/4.7/4.6, Sonnet 5, Sonnet 4.6.
# An unlisted ID takes the 1M default; see docs/handoff-nudge.md for why.
# Each arm requires an exact match or a trailing "-" (dated-snapshot suffix),
# not a bare trailing "*", so a longer numeral (claude-opus-4-10) can't
# collide with a shorter one (claude-opus-4-1) by string prefix alone.
# Both verified lists are enumerated arms, so the default arm means "no entry
# for this ID" rather than "1M model": without the 1M arm every 1M model would
# be indistinguishable from an unknown one, and --check would report the most
# common models as defaulted.
# Sets CONTEXT_WINDOW, and MODEL_RECOGNIZED=0 only on the default arm —
# --check reports that as a defaulted window rather than a resolved one, which
# the fire path has no way to say.
resolve_context_window() {
  case "$1" in
    claude-haiku-4-5|claude-haiku-4-5-*| \
    claude-sonnet-4-5|claude-sonnet-4-5-*| \
    claude-opus-4-5|claude-opus-4-5-*| \
    claude-opus-4-1|claude-opus-4-1-*)
      CONTEXT_WINDOW=200000
      MODEL_RECOGNIZED=1 ;;
    claude-fable-5|claude-fable-5-*| \
    claude-mythos-5|claude-mythos-5-*| \
    claude-opus-5|claude-opus-5-*| \
    claude-opus-4-8|claude-opus-4-8-*| \
    claude-opus-4-7|claude-opus-4-7-*| \
    claude-opus-4-6|claude-opus-4-6-*| \
    claude-sonnet-5|claude-sonnet-5-*| \
    claude-sonnet-4-6|claude-sonnet-4-6-*)
      CONTEXT_WINDOW=1000000
      MODEL_RECOGNIZED=1 ;;
    *)
      CONTEXT_WINDOW=1000000
      MODEL_RECOGNIZED=0 ;;
  esac
}

# Cache-read cost is linear in absolute tokens, so pct-of-window alone lets
# the same rule fire 5x later in dollar terms on a 1M-window model than a
# 200k one; ABS_CAP bounds the pct arm — see docs/handoff-nudge.md "Why this
# cap" for the grounding. HANDOFF_NUDGE_ABS_CAP overrides the cap; a
# malformed value (empty, a literal zero, non-digit, zero-padded — an invalid
# octal literal in bash arithmetic — or 10+ digits, which risks wrapping
# negative in bash's signed 64-bit arithmetic) falls back to the default
# rather than letting THRESHOLD degrade toward 0/unset or negative, either of
# which fires on every session.
# CONTEXT_WINDOW and PCT_THRESHOLD keep those exact names against the `local`
# convention in claude/.claude/rules/shell-script-conventions.md:
# test_doc_counts.py source-scans this file for the literal
# `PCT_THRESHOLD=$(( CONTEXT_WINDOW * N / 100 ))` and raises if it is absent.
compute_threshold() {
  PCT_THRESHOLD=$(( CONTEXT_WINDOW * 40 / 100 ))
  case "$HANDOFF_NUDGE_ABS_CAP" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) ABS_CAP=360000 ;;
    *) ABS_CAP=$HANDOFF_NUDGE_ABS_CAP ;;
  esac
  THRESHOLD=$(( PCT_THRESHOLD < ABS_CAP ? PCT_THRESHOLD : ABS_CAP ))
}

# Re-arm spacing between nudges past the first fire — see
# docs/handoff-nudge.md "Why this spacing" for the grounding.
# HANDOFF_NUDGE_REARM_SPACING overrides it; same malformed-value guard as
# HANDOFF_NUDGE_ABS_CAP above, for the same reason (a degraded spacing
# toward 0 would re-fire on every turn; 10+ digits risks wrapping negative).
resolve_rearm_spacing() {
  case "$HANDOFF_NUDGE_REARM_SPACING" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) REARM_SPACING=80000 ;;
    *) REARM_SPACING=$HANDOFF_NUDGE_REARM_SPACING ;;
  esac
}

# Escalation-block threshold: past this many ignored re-arms in one session,
# a further re-arm hard-blocks instead of advising — see
# docs/handoff-nudge.md "Why this block-after count" for the grounding.
# HANDOFF_NUDGE_BLOCK_AFTER overrides it; same malformed-value guard as
# HANDOFF_NUDGE_ABS_CAP/HANDOFF_NUDGE_REARM_SPACING above, for the same
# reason (a degraded value toward 0 would hard-block on the very first
# re-arm; 10+ digits risks wrapping negative).
resolve_block_after() {
  case "$HANDOFF_NUDGE_BLOCK_AFTER" in
    ''|0|*[!0-9]*|0[0-9]*|?????????*) BLOCK_AFTER=1 ;;
    *) BLOCK_AFTER=$HANDOFF_NUDGE_BLOCK_AFTER ;;
  esac
}

# The jq filter both read_latest_usage's bounded bootstrap scan and
# read_latest_usage_cached's incremental scan below apply to their own
# respective JSONL slice: the most recent assistant record carrying a usage
# block.
_USAGE_BLOCK_JQ_FILTER='map(select(.message? and .message.usage)) | last // empty'

# Reads the latest assistant usage block from the last 200 lines of $1 and
# sets ESTIMATE and MODEL. Returns 1 when no usage block exists or the token
# sum could not be read, leaving the caller to pick its own failure idiom.
# ESTIMATE is read first: a corrupted or multi-line MODEL value can only
# truncate MODEL, never desync ESTIMATE. Used directly by --check (whose own
# contract is a fresh, uncached number, never the fire path's cached one) and
# as read_latest_usage_cached's own bootstrap step below.
read_latest_usage() {
  local transcript_path="$1"
  local usage_block
  usage_block=$(_lib_capped_for 2 tail -n 200 "$transcript_path" 2>/dev/null \
    | jq -s "$_USAGE_BLOCK_JQ_FILTER" 2>/dev/null)
  [ -n "$usage_block" ] || return 1
  ESTIMATE=""
  MODEL=""
  {
    IFS= read -r ESTIMATE
    IFS= read -r MODEL
  } < <(
    printf '%s\n' "$usage_block" \
      | jq -r '
          ((.message.usage.cache_read_input_tokens // 0)
         + (.message.usage.cache_creation_input_tokens // 0)
         + (.message.usage.input_tokens // 0)
         + (.message.usage.output_tokens // 0)),
          (.message.model // "" | tostring | gsub("[^a-zA-Z0-9._-]"; ""))
        ' 2>/dev/null
  ) 2>/dev/null || true
  [ -n "$ESTIMATE" ] || return 1
  return 0
}

# _advance_offset_past_complete_lines TRANSCRIPT OFFSET CURRENT_SIZE
# Prints the resume-from byte offset, stopping before any trailing
# partially-written line — see docs/handoff-nudge.md "What the hook does"
# for the incremental-read mechanism this supports.
# Known limitation: on a scan timeout in the slow path below, this returns
# OFFSET unchanged rather than partial progress — see docs/handoff-nudge.md
# "Known limitations" for the retry-cost tradeoff that follows from that.
_advance_offset_past_complete_lines() {
  local transcript_path="$1" offset="$2" current_size="$3"
  if [ "$current_size" -le "$offset" ] 2>/dev/null; then
    printf '%s' "$offset"
    return
  fi
  # Fast path: the file's current last byte is a newline, so everything up
  # to current_size is complete lines — one 1-byte read covers the common
  # case (Claude Code writes each transcript record as a complete line).
  local last_byte
  last_byte=$(_lib_capped_for 2 tail -c 1 "$transcript_path" 2>/dev/null)
  if [ -z "$last_byte" ]; then
    printf '%s' "$current_size"
    return
  fi
  # Slow path: the file currently ends mid-line (caught mid-write). Count
  # complete lines in the unread slice, then measure exactly that many bytes
  # with `head`/`wc -c` — avoids locale-sensitive string-length arithmetic on
  # a captured shell variable.
  local newline_count complete_bytes
  newline_count=$(_lib_capped_for 2 tail -c +$((offset + 1)) "$transcript_path" 2>/dev/null \
    | tr -cd '\n' | wc -c | tr -d '[:space:]')
  case "$newline_count" in ''|*[!0-9]*|0) printf '%s' "$offset"; return ;; esac
  complete_bytes=$(_lib_capped_for 2 tail -c +$((offset + 1)) "$transcript_path" 2>/dev/null \
    | head -n "$newline_count" | wc -c | tr -d '[:space:]')
  case "$complete_bytes" in ''|*[!0-9]*) printf '%s' "$offset"; return ;; esac
  printf '%s' "$(( offset + complete_bytes ))"
}

# read_latest_usage_cached TRANSCRIPT SESSION_ID MARKER_DIR
# Fire-path-only wrapper around read_latest_usage: sets ESTIMATE and MODEL
# via an incremental byte-offset scan instead of a full re-scan — see
# docs/handoff-nudge.md "What the hook does" for why and the state-file
# format. --check keeps calling read_latest_usage directly, unaffected.
# SESSION_ID/MARKER_DIR are taken as explicit arguments, not read from the
# fire path's own globals of the same name, so this function has no implicit
# ordering dependency on when the fire path assigns them.
read_latest_usage_cached() {
  local transcript_path="$1" session_id="$2" marker_dir="$3"
  local scan_state="${marker_dir}/${session_id}-scan"
  local stored_offset="" cached_estimate="" cached_model=""
  if [ -f "$scan_state" ]; then
    {
      IFS= read -r stored_offset
      IFS= read -r cached_estimate
      IFS= read -r cached_model
    } < "$scan_state" 2>/dev/null || true
  fi
  case "$stored_offset" in ''|*[!0-9]*) stored_offset="" ;; esac

  local current_size
  current_size=$(_lib_capped_for 2 wc -c < "$transcript_path" 2>/dev/null | tr -d '[:space:]')
  case "$current_size" in ''|*[!0-9]*) current_size="" ;; esac
  [ -n "$current_size" ] || return 1

  local scan_from=0 bootstrap=1
  if [ -n "$stored_offset" ] && [ "$stored_offset" -le "$current_size" ] 2>/dev/null; then
    scan_from="$stored_offset"
    bootstrap=0
  fi

  local usage_found=0
  if [ "$bootstrap" -eq 1 ]; then
    read_latest_usage "$transcript_path" && usage_found=1
  else
    local usage_block
    usage_block=$(_lib_capped_for 2 tail -c +$((scan_from + 1)) "$transcript_path" 2>/dev/null \
      | jq -s "$_USAGE_BLOCK_JQ_FILTER" 2>/dev/null)
    if [ -n "$usage_block" ]; then
      ESTIMATE=""
      MODEL=""
      {
        IFS= read -r ESTIMATE
        IFS= read -r MODEL
      } < <(
        printf '%s\n' "$usage_block" \
          | jq -r '
              ((.message.usage.cache_read_input_tokens // 0)
             + (.message.usage.cache_creation_input_tokens // 0)
             + (.message.usage.input_tokens // 0)
             + (.message.usage.output_tokens // 0)),
              (.message.model // "" | tostring | gsub("[^a-zA-Z0-9._-]"; ""))
            ' 2>/dev/null
      ) 2>/dev/null || true
      [ -n "$ESTIMATE" ] && usage_found=1
    fi
  fi
  if [ "$usage_found" -eq 0 ]; then
    ESTIMATE="$cached_estimate"
    MODEL="$cached_model"
  fi

  local new_offset
  new_offset=$(_advance_offset_past_complete_lines "$transcript_path" "$scan_from" "$current_size")
  case "$new_offset" in ''|*[!0-9]*) new_offset="$scan_from" ;; esac

  mkdir -p "$marker_dir" 2>/dev/null || true
  printf '%s\n%s\n%s\n' "$new_offset" "$ESTIMATE" "$MODEL" > "$scan_state" 2>/dev/null || true

  [ -n "$ESTIMATE" ] || return 1
  return 0
}

# Measured need is 2 hops (invoked straight from a Bash tool call) or 3
# (through a subshell); 6 leaves headroom for wrapper shells while keeping a
# wedged process table a fast refusal rather than a hang.
CHECK_MAX_ANCESTOR_HOPS=6

# Emits a cannot-resolve object and exits. The fallback literal is fixed
# ASCII, so it needs no encoding and stays correct when jq is what failed.
check_refuse() {
  local out
  # shellcheck disable=SC2016 # single-quoted on purpose: $reason is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
  out=$(_lib_capped_for 2 jq -n --arg reason "$1" '{status:"cannot-resolve",reason:$reason}' 2>/dev/null)
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  else
    printf '{"status":"cannot-resolve","reason":"jq-unavailable"}\n'
  fi
  exit 0
}

run_check_mode() {
  [ -n "$CONFIG_DIR" ] || check_refuse "config-dir-unresolved"

  # The harness gives hooks their session_id on stdin, but a manual run gets
  # nothing, so walk ancestors for the claude PID's sessions/<pid> entry.
  local pid=$PPID hop=0 session_id="" stored_start="" entry
  local ps_line ancestor_ppid ancestor_comm
  while [ "$hop" -lt "$CHECK_MAX_ANCESTOR_HOPS" ]; do
    hop=$(( hop + 1 ))
    case "$pid" in ''|*[!0-9]*) break ;; esac
    [ "$pid" -gt 1 ] 2>/dev/null || break
    entry="$CONFIG_DIR/sessions/$pid"
    if [ -f "$entry" ]; then
      session_id=$(head -n1 "$entry" 2>/dev/null)
      stored_start=$(sed -n '2p' "$entry" 2>/dev/null)
      break
    fi
    # comm rides along with the parent PID so the walk stays at one ps per hop.
    ps_line=$(_lib_capped ps -o ppid=,comm= -p "$pid" 2>/dev/null)
    # Bare `read`, not the conventional `IFS= read`: field splitting is the
    # point here, and IFS= would put the whole line in the first name.
    read -r ancestor_ppid ancestor_comm <<<"$ps_line"
    # A session missing its own entry must refuse rather than inherit its
    # parent session's, so the walk stops here instead of climbing past claude.
    # Each arm is a real rendering: GNU ps reports a bare name, BSD an absolute
    # path, and either prefixes a hyphen when argv[0] does. No `-*/claude` arm —
    # `*/claude` already matches that, and shellcheck rejects it as unreachable.
    case "$ancestor_comm" in
      claude|*/claude|-claude) check_refuse "session-id-missing-at-claude" ;;
    esac
    pid=$ancestor_ppid
  done

  [ -n "$session_id" ] || check_refuse "session-id-unresolved"
  # Validated before it becomes a glob and path component below.
  _lib_valid_session_id_component "$session_id" || check_refuse "session-id-malformed"

  # PID reuse would otherwise bind a confident number to the wrong session.
  # Routed through `env` because `timeout` execs a leading VAR=val as the
  # program name (exit 127); `env` applies the pinned TZ/locale that
  # capture-session-id.sh writes the stored value under, while keeping
  # _lib_capped's cap so a wedged process table refuses instead of hanging.
  local live_start
  live_start=$(_lib_capped env TZ=UTC LC_ALL=C ps -o lstart= -p "$pid" 2>/dev/null)
  if [ -z "$stored_start" ] || [ -z "$live_start" ] || [ "$stored_start" != "$live_start" ]; then
    check_refuse "session-id-stale-pid"
  fi

  # Keying the transcript on the session id skips project-slug derivation
  # entirely, so a session that moved between worktrees still resolves.
  # nullglob is load-bearing: bash's default expands a zero-match pattern to
  # the literal pattern string, which would read as exactly one match.
  # Positional parameters carry the matches so the count is $#, avoiding the
  # array-length form that deny-private-project-refs.sh reads as a
  # Slack-channel reference.
  # noglob is saved and cleared alongside nullglob: --check runs from an
  # arbitrary Bash tool call, and a caller with `set -f` would leave the
  # pattern unexpanded — one match holding the literal pattern, which reads as
  # a found transcript and misreports the failure as usage-block-missing.
  local nullglob_was_set=0 noglob_was_set=0
  if shopt -q nullglob; then nullglob_was_set=1; fi
  case $- in *f*) noglob_was_set=1 ;; esac
  shopt -s nullglob
  set +f
  set -- "$CONFIG_DIR"/projects/*/"$session_id".jsonl
  if [ "$nullglob_was_set" -eq 0 ]; then shopt -u nullglob; fi
  if [ "$noglob_was_set" -eq 1 ]; then set -f; fi
  [ "$#" -gt 0 ] || check_refuse "transcript-not-found"
  [ "$#" -eq 1 ] || check_refuse "transcript-ambiguous"

  read_latest_usage "$1" || check_refuse "usage-block-missing"

  local nudge_disabled=false
  [ -f "$CONFIG_DIR/.handoff-nudge-disabled" ] && nudge_disabled=true

  # Same all-fields-zero condition the fire path logs as schema drift, minus
  # the marker and log write.
  if [ "$ESTIMATE" -eq 0 ] 2>/dev/null; then
    local drift_out
    # shellcheck disable=SC2016 # single-quoted on purpose: $session is a jq --arg binding, not a shell variable; double-quoting would expand it in the shell before jq sees it. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
    drift_out=$(_lib_capped_for 2 jq -n --arg session "$session_id" \
      '{status:"schema-drift",session_id:$session}' 2>/dev/null)
    if [ -n "$drift_out" ]; then
      printf '%s\n' "$drift_out"
    else
      printf '{"status":"cannot-resolve","reason":"jq-unavailable"}\n'
    fi
    exit 0
  fi

  resolve_context_window "$MODEL"
  compute_threshold

  local over_threshold=false
  [ "$ESTIMATE" -ge "$THRESHOLD" ] 2>/dev/null && over_threshold=true
  local model_recognized=false
  [ "$MODEL_RECOGNIZED" -eq 1 ] && model_recognized=true
  local already_fired=false
  [ -f "$CONFIG_DIR/.handoff-nudge-fired.d/$session_id" ] && already_fired=true

  local out
  # shellcheck disable=SC2016 # single-quoted on purpose: every $-prefixed name below is a jq --arg/--argjson binding, not a shell variable; double-quoting would expand them in the shell before jq sees them. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
  out=$(_lib_capped_for 2 jq -n \
    --arg session "$session_id" \
    --arg model "$MODEL" \
    --argjson estimate "$ESTIMATE" \
    --argjson threshold "$THRESHOLD" \
    --argjson window "$CONTEXT_WINDOW" \
    --argjson over "$over_threshold" \
    --argjson recognized "$model_recognized" \
    --argjson fired "$already_fired" \
    --argjson disabled "$nudge_disabled" \
    '{status:"ok",session_id:$session,estimate:$estimate,threshold:$threshold,
      over_threshold:$over,model:$model,context_window:$window,
      model_recognized:$recognized,already_fired:$fired,nudge_disabled:$disabled}' 2>/dev/null)
  if [ -n "$out" ]; then
    printf '%s\n' "$out"
  else
    printf '{"status":"cannot-resolve","reason":"jq-unavailable"}\n'
  fi
  exit 0
}

if [ "$CHECK_MODE" -eq 1 ]; then
  run_check_mode
fi

INPUT=$(cat 2>/dev/null)

# Extract all five fields in a single jq pass to avoid five separate subshell spawns.
# Sequential reads from the jq output: each field on its own line handles
# empty values and paths with spaces correctly. Pre-initialize to "" so a
# failed read (e.g. jq unavailable or INPUT invalid) leaves empty strings
# rather than unbound variables. The || true preserves fail-open semantics.
SESSION_ID=""
AGENT_TYPE=""
PERMISSION_MODE=""
TRANSCRIPT_PATH=""
HOOK_EVENT=""
{
  IFS= read -r SESSION_ID
  IFS= read -r AGENT_TYPE
  IFS= read -r PERMISSION_MODE
  IFS= read -r TRANSCRIPT_PATH
  IFS= read -r HOOK_EVENT
} < <(
  printf '%s\n' "$INPUT" \
    | _lib_capped_for 2 jq -r '(.session_id // ""),(.agent_type // ""),(.permission_mode // ""),(.transcript_path // ""),(.hook_event_name // "")' \
    2>/dev/null
) 2>/dev/null || true
[ -z "$SESSION_ID" ] && exit 0

# Constrain to the two registered events; default to UserPromptSubmit for an
# empty, missing, or unrecognized value (matches SESSION_ID/MODEL's own
# allowlist treatment of this same untrusted jq-extracted input). This
# fallback also guards the landing-order hazard named in the header above: if
# settings.json's PostToolBatch registration were ever to land before this
# case arm does, an unrecognized event value degrades to the same
# UserPromptSubmit label every caller already handles, rather than an
# unlabeled misfire.
case "$HOOK_EVENT" in
  PostToolBatch|Stop) ;;
  *) HOOK_EVENT="UserPromptSubmit" ;;
esac

# SESSION_ID feeds DRIFT_MARKER and FIRED_MARKER below as a path component
# ("../" would escape MARKER_DIR); fail the same way an empty id already does.
_lib_valid_session_id_component "$SESSION_ID" || exit 0

# No config dir means no kill-switch to read and nowhere to write the marker
# or log, so fail open exactly as an unusable SESSION_ID already does.
[ -n "$CONFIG_DIR" ] || exit 0

# Kill-switch: suppress nudge for automated pipelines or user opt-out.
if [ -f "$CONFIG_DIR/.handoff-nudge-disabled" ]; then
  exit 0
fi

# Subagent gate: only nudge in the main session, not in subagents.
if [ -n "$AGENT_TYPE" ]; then
  exit 0
fi

# Plan-mode gate: nudging in plan mode would interrupt planning flow.
if [ "$PERMISSION_MODE" = "plan" ]; then
  exit 0
fi

# Transcript read: get the latest assistant usage block from the transcript.
if [ -z "$TRANSCRIPT_PATH" ] || [ ! -f "$TRANSCRIPT_PATH" ]; then
  exit 0
fi

# Ensure the log parent directory exists before any log write, and assign
# MARKER_DIR before read_latest_usage_cached below — its incremental-scan
# state file needs this directory to exist and be known.
mkdir -p "$CONFIG_DIR" 2>/dev/null || true
NUDGE_LOG="$CONFIG_DIR/.handoff-nudge.log"
MARKER_DIR="$CONFIG_DIR/.handoff-nudge-fired.d"

# Evict stale markers from one-shot runs that skipped SessionEnd cleanup.
# Runs on every invocation that reaches this point, not only on a fire,
# because the -scan state file needs the same bound as FIRED_MARKER/
# DRIFT_MARKER/-ignored — see docs/handoff-nudge.md "Known limitations" for
# the resulting marker-directory growth shape.
mkdir -p "$MARKER_DIR" 2>/dev/null || true
find "$MARKER_DIR" -maxdepth 1 -mtime +30 -delete 2>/dev/null || true

read_latest_usage_cached "$TRANSCRIPT_PATH" "$SESSION_ID" "$MARKER_DIR" || exit 0

# Schema-drift detection: usage block present but all four fields are 0 or null.
# This indicates the transcript schema changed and the field paths are stale.
if [ "$ESTIMATE" -eq 0 ] 2>/dev/null; then
  DRIFT_MARKER="${MARKER_DIR}/${SESSION_ID}-drift"
  if [ ! -f "$DRIFT_MARKER" ]; then
    printf 'schema-drift session=%s event=%s\n' "$SESSION_ID" "$HOOK_EVENT" >> "$NUDGE_LOG" 2>/dev/null || true
    mkdir -p "$MARKER_DIR" 2>/dev/null || true
    touch "$DRIFT_MARKER" 2>/dev/null || true
  fi
  exit 0
fi

resolve_context_window "$MODEL"
compute_threshold

if [ "$ESTIMATE" -lt "$THRESHOLD" ] 2>/dev/null; then
  exit 0
fi

# Already fired: suppress until the estimate has advanced REARM_SPACING
# tokens past the last fire; re-arms rather than staying silent forever.
resolve_rearm_spacing
FIRED_MARKER="${MARKER_DIR}/${SESSION_ID}"
LAST_FIRED_AT=""
if [ -f "$FIRED_MARKER" ]; then
  IFS= read -r LAST_FIRED_AT < "$FIRED_MARKER" 2>/dev/null || LAST_FIRED_AT=""
fi
# A corrupt or unreadable marker forces a fire rather than suppressing one
# (fail toward firing, never toward silent suppression). A literal `0`
# marker is never legitimate — ESTIMATE is always >= THRESHOLD > 0 at fire
# time — so it must not be read as a real LAST_FIRED_AT, which would
# suppress future fires under a large HANDOFF_NUDGE_REARM_SPACING override.
case "$LAST_FIRED_AT" in ''|0|*[!0-9]*|0[0-9]*|?????????*) LAST_FIRED_AT="" ;; esac
if [ -n "$LAST_FIRED_AT" ] && [ "$ESTIMATE" -lt "$(( LAST_FIRED_AT + REARM_SPACING ))" ] 2>/dev/null; then
  exit 0
fi

# Escalation ladder: every fire past the first (LAST_FIRED_AT non-empty) is
# a re-arm the session ignored — append one byte to IGNORED_MARKER to count
# it. O_APPEND writes are atomic under POSIX for a write this small, closing
# the lost-update race a read-modify-write counter would have.
resolve_block_after
IGNORED_MARKER="${MARKER_DIR}/${SESSION_ID}-ignored"
if [ -n "$LAST_FIRED_AT" ]; then
  printf '.' >> "$IGNORED_MARKER" 2>/dev/null || true
fi
IGNORED_COUNT=0
if [ -f "$IGNORED_MARKER" ]; then
  IGNORED_COUNT=$(_lib_capped_for 2 wc -c < "$IGNORED_MARKER" 2>/dev/null | tr -d '[:space:]')
  case "$IGNORED_COUNT" in ''|*[!0-9]*) IGNORED_COUNT=0 ;; esac
fi

if [ "$IGNORED_COUNT" -ge "$BLOCK_AFTER" ] 2>/dev/null && [ "$HOOK_EVENT" = "PostToolBatch" ]; then
  # Hard block: PostToolBatch's own exit-2 contract stops the agentic loop
  # before the next model call, with no JSON envelope. Guarded to
  # PostToolBatch only — on Stop, exit 2 forces the conversation to
  # continue instead of blocking it, so that registration falls through to
  # the advisory fire path below.
  # A hook_event_name value degraded away from PostToolBatch by something
  # other than a genuine Stop registration would silently fall through to
  # advisory-only here too, with no distinguishing log signal.
  printf 'nudged session=%s est=%s model=%s window=%s event=%s action=block\n' \
    "$SESSION_ID" "$ESTIMATE" "$MODEL" "$CONTEXT_WINDOW" "$HOOK_EVENT" >> "$NUDGE_LOG" 2>/dev/null || true
  printf '%s\n' "$ESTIMATE" > "$FIRED_MARKER" 2>/dev/null || true
  printf 'Context is past this session'\''s handoff-nudge threshold (%s tokens), and %s prior re-arms went unacted on this session (HANDOFF_NUDGE_BLOCK_AFTER=%s). Blocking rather than advising: run /handoff now — it captures state in a /tmp file and resumes in a fresh session.\n' \
    "$THRESHOLD" "$IGNORED_COUNT" "$BLOCK_AFTER" >&2
  exit 2
fi

# Fire: build the nudge JSON first and only write the marker/log if it
# actually produced output — jq -n … 2>/dev/null below would otherwise
# swallow a jq failure while the marker/log writes had already burned the
# session's one shot. timeout 2 matches the tail | jq -s call above.
# shellcheck disable=SC2016 # single-quoted on purpose: $hookEventName/$threshold are jq --arg/--argjson bindings, not shell variables; double-quoting would expand them in the shell before jq sees them. Bare `jq` suppresses this itself, but the _lib_capped_for wrapper that carries the timeout backstop is opaque to shellcheck's jq awareness.
OUTPUT=$(_lib_capped_for 2 jq -n --arg hookEventName "$HOOK_EVENT" --argjson threshold "$THRESHOLD" '{
  hookSpecificOutput: {
    hookEventName: $hookEventName,
    additionalContext: ("Context is past this session'\''s handoff-nudge threshold (" + ($threshold|tostring) + " tokens). If the current task is not close to done, suggest running /handoff to the user — it captures state in a /tmp file and resumes in a fresh session. Per-turn cost rises with carried context, but a fresh session pays a one-time rebuild cost first, so handoff pays off over the next several turns rather than immediately. If the task is nearly complete, ignore this and finish.")
  }
}' 2>/dev/null) && [ -n "$OUTPUT" ] && {
  printf 'nudged session=%s est=%s model=%s window=%s event=%s\n' \
    "$SESSION_ID" "$ESTIMATE" "$MODEL" "$CONTEXT_WINDOW" "$HOOK_EVENT" >> "$NUDGE_LOG" 2>/dev/null || true
  printf '%s\n' "$ESTIMATE" > "$FIRED_MARKER" 2>/dev/null || true
  printf '%s' "$OUTPUT"
}

exit 0
