#!/bin/bash
# Append-only review-narrative ledger: /code-review appends one line per
# finding-disposition event as it runs, so a mid-review compaction or
# session resume doesn't lose which findings were raised, how they were
# dispositioned, and why.
# Usage: review-ledger.sh <append|show|clear-stale> [args]
# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

set -u

# ${#VAR} below counts codepoints under a UTF-8 locale but bytes under
# C/POSIX -- these caps are not pinned to either, so the effective limit
# tracks whichever locale invokes this script.
_LEDGER_FINDING_MAX_CHARS=200
_LEDGER_RATIONALE_MAX_CHARS=300
_LEDGER_SOURCE_MAX_CHARS=200
# 4 digits (max round 9999) is generous for a branch-scoped invocation counter.
_LEDGER_ROUND_MAX_DIGITS=4
# Longest built JSON line append accepts, excluding its newline.
# Line plus newline must fit one write(2), because only a single O_APPEND
# write lands contiguously against concurrent appenders.
# Bash's printf builtin emits a line in one write only while it fits its stdio
# buffer, which is the file's st_blksize (4096 on common local filesystems).
# The single-write guarantee therefore holds only when that buffer is at least
# the line length.
# NFS has no atomic O_APPEND, and a filesystem can report an st_blksize below 4096.
# In either case the append lock is the only other protection.
# That lock fails open after _LIB_APPEND_LOCK_RETRIES failed acquisitions.
_LEDGER_LINE_MAX_BYTES=4095
# Ledger row schema version -- unread today, lets a future migration
# distinguish row shapes without re-deriving them from optional-key presence.
_LEDGER_SCHEMA_VERSION=3

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-ledger.sh <subcommand> [args]

Subcommands:
  append code-review --disposition ADDRESS|DEFER|CLEAN --round <N> \
      [--finding <text> --rationale <text>] [--source <file:line>] \
      [--authoring-agent code-writer|inline|mixed|unknown] \
      [--authoring-effort low|medium|high|xhigh]
             Append one finding-disposition event to this branch's ledger
             (this session's ledger on a detached HEAD or the default branch).
             --finding/--rationale are required for ADDRESS|DEFER;
             --finding/--rationale/--source must be omitted for CLEAN.
             --round is the 1-based review round number for this
             /code-review run on this branch.
             No-ops (exit 0) if the identical line (by round, finding,
             disposition, rationale, source, authoring_agent,
             authoring_effort, session_id) already exists.
             --authoring-agent and --authoring-effort are optional; an
             absent flag never aborts the append, but an invalid value does.
  show       Print this branch's ledger rows (this session's rows on a
             detached HEAD or the default branch) sorted by event_time, each
             tagged with its own source_repo_hash, or an absence message. A
             header line on stderr always has the shape
             `show scope=<branch|session> rows=<N> oldest=<time|-> newest=<time|->
             max_round=<N> files=<comma-joined paths>`. With no ledger it
             reads rows=0 max_round=0 and an empty files=. files= comes last
             because a path can hold spaces, commas, or '='. An undecodable
             line is skipped and not counted. If jq fails, show
             prints the rows without a header and exits 1: the round count is
             then unknown, not 0.
  clear-stale [--dry-run]
             Remove ledger (.jsonl) and orphaned lock (.lock) files older
             than the resolved sweep window (Claude Code's cleanupPeriodDays
             setting, floored at 30 days), across every repo-hash.
             --dry-run reports without removing.
EOF
}

# Mirrors marker.sh's _resolve_session_id shape (same _lib.sh helpers, this
# script's own error text) rather than sourcing marker.sh itself — scripts
# in this repo each define their own thin wrapper over the shared _lib.sh
# primitives.
_resolve_session_id() {
  local out sid
  out=$(_lib_resolve_claude_pid) || {
    printf 'review-ledger.sh: SESSION_ID empty — capture-session-id.sh SessionStart hook did not run. Abort without writing.\n' >&2
    return 2
  }
  sid="${out%% *}"
  # Chokepoint for every path built from a session id below: an id
  # containing '..' or '/' would escape the ledger directory once
  # concatenated into a path.
  if ! _lib_valid_session_id_component "$sid"; then
    printf 'review-ledger.sh: SESSION_ID %s is not a valid path component. Abort without writing.\n' "$sid" >&2
    return 2
  fi
  printf '%s' "$sid"
}

_resolve_repo_root() {
  local root
  root=$(git rev-parse --show-toplevel 2>/dev/null | tr -d '\n')
  if [ -z "$root" ]; then
    printf 'review-ledger.sh: not inside a git repository\n' >&2
    return 2
  fi
  printf '%s' "$root"
}

# _resolve_ledger_location REPO_ROOT SESSION_ID
# Sets LEDGER_SCOPE (branch|session) and LEDGER_FILE from
# _lib_review_ledger_path (_lib.sh), which owns the key and fallback rules.
_resolve_ledger_location() {
  local out
  out=$(_lib_review_ledger_path "$CONFIG_DIR" "$1" "$2") || {
    # The session path shares the repo-hash and session-id steps, so when it
    # also fails the cause is one of those rather than the HEAD read.
    if _lib_review_ledger_session_path "$CONFIG_DIR" "$1" "$2" >/dev/null; then
      printf 'review-ledger.sh: git could not read HEAD or derive the branch key for this repository (a stalled or failing git). Retry once. Abort without writing.\n' >&2
    else
      printf 'review-ledger.sh: could not resolve the ledger file for this repository (repo hash or session id failed). Abort without writing.\n' >&2
    fi
    return 2
  }
  LEDGER_SCOPE="${out%% *}"
  LEDGER_FILE="${out#* }"
}

# _is_uint VALUE: true iff VALUE is one or more decimal digits.
_is_uint() {
  case "$1" in
    ''|*[!0-9]*) return 1 ;;
  esac
}

# _reject_missing_round [BAD_VALUE]
# Prints the --round error text and returns; caller still exits 2.
# - No argument: --round was never passed (drops the "or invalid" clause
#   and the value).
# - BAD_VALUE given: --round was passed but failed validation.
_reject_missing_round() {
  local bad_value="${1:-}"
  if [ -n "$bad_value" ]; then
    printf "review-ledger.sh: --round <N> is required and missing (or invalid: got '%s').\n" "$bad_value" >&2
  else
    printf 'review-ledger.sh: --round <N> is required and missing.\n' >&2
  fi
  cat >&2 <<'EOF'

--round is the 1-based review round number for *this* /code-review run on
this branch: 1 for the branch's first round, then one past the highest round
`review-ledger.sh show` reports, once per subsequent /code-review invocation.
Retry the same append with --round <N> added to whatever other flags you
already passed.

Required so two rounds raising an identical finding don't collapse into one
ledger line under this script's round-scoped dedup key. Abort without writing.
EOF
}

# _sweep_stale_ledger_files LEDGER_DIR WINDOW_DAYS DRY_RUN REPORT
# Removes (or, if DRY_RUN=1, reports without removing) every *.jsonl and
# *.lock file under LEDGER_DIR older than WINDOW_DAYS by mtime, across every
# repo-hash. Shaped like nudge-handoff-near-context-cap.sh's directory-wide
# `find ... -mtime +30 -delete` sweep of .handoff-nudge-fired.d, except this
# window is a caller-resolved value rather than a fixed 30. The best-effort
# append path passes the fixed _LEDGER_SWEEP_FLOOR_DAYS floor directly.
# clear-stale resolves _ledger_sweep_window_days' dynamic settings.json read
# itself and passes the result. REPORT=1 prints per-file and summary lines
# (clear-stale). REPORT=0 is silent (the best-effort sweep append performs
# on every invocation).
_sweep_stale_ledger_files() {
  local ledger_dir="$1" window_days="$2" dry_run="$3" report="$4"
  [ -d "$ledger_dir" ] || return 0
  local evicted=0 entry
  while IFS= read -r -d '' entry; do
    evicted=$((evicted + 1))
    if [ "$dry_run" -eq 1 ]; then
      [ "$report" -eq 1 ] && printf '  evict (dry-run): %s\n' "$(basename "$entry")"
    else
      rm -f "$entry" 2>/dev/null
      [ "$report" -eq 1 ] && printf '  evict: %s\n' "$(basename "$entry")"
    fi
  done < <(find "$ledger_dir" -maxdepth 1 \( -name '*.jsonl' -o -name '*.lock' \) -mtime "+$window_days" -print0 2>/dev/null)
  if [ "$report" -eq 1 ]; then
    if [ "$dry_run" -eq 1 ]; then
      printf 'clear-stale: would evict %d file(s)\n' "$evicted"
    else
      printf 'clear-stale: evicted %d file(s)\n' "$evicted"
    fi
  fi
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ $# -lt 1 ]; then
  usage
  exit 2
fi

# Fail closed: every path below is built from CONFIG_DIR, so an
# unresolvable CLAUDE_CONFIG_DIR (relative value, or empty $HOME with no
# override) must abort rather than fall through to a root-anchored path.
CONFIG_DIR=$(_lib_config_dir) || {
  # shellcheck disable=SC2016 # single-quoted for literal display text: $HOME
  # and $CLAUDE_CONFIG_DIR name the env vars in the message, not shell expansions.
  printf 'review-ledger.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or $HOME is unset/empty). Abort without writing.\n' >&2
  exit 2
}
LEDGER_DIR="$CONFIG_DIR/review-narrative-ledger"

SUBCOMMAND="$1"
shift

case "$SUBCOMMAND" in
  append)
    GATE="${1:-}"
    if [ "$GATE" != "code-review" ]; then
      printf "review-ledger.sh: 'append %s' is not valid. 'append' supports: code-review\n" "$GATE" >&2
      usage
      exit 2
    fi
    shift

    FINDING=""
    DISPOSITION=""
    RATIONALE=""
    SOURCE="n/a"
    ROUND=""
    # Empty means the caller omitted --authoring-agent/--authoring-effort ("not declared"), not that it declared and confirmed empty.
    AUTHORING_AGENT=""
    AUTHORING_EFFORT=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --finding|--disposition|--rationale|--source|--round|--authoring-agent|--authoring-effort)
          if [ $# -lt 2 ]; then
            printf "review-ledger.sh: %s requires a value\n" "$1" >&2
            exit 2
          fi
          ;;
      esac
      case "$1" in
        --finding) FINDING="$2"; shift 2 ;;
        --disposition) DISPOSITION="$2"; shift 2 ;;
        --rationale) RATIONALE="$2"; shift 2 ;;
        --source) SOURCE="$2"; shift 2 ;;
        --round) ROUND="$2"; shift 2 ;;
        --authoring-agent) AUTHORING_AGENT="$2"; shift 2 ;;
        --authoring-effort) AUTHORING_EFFORT="$2"; shift 2 ;;
        # Unrecognized flags hard-reject (exit 2): this repo's stow model
        # keeps SKILL.md and this script co-committed, so cross-version
        # skew doesn't arise in practice.
        *)
          printf "review-ledger.sh: unknown argument '%s'\n" "$1" >&2
          usage
          exit 2
          ;;
      esac
    done

    case "$ROUND" in
      '')
        _reject_missing_round
        exit 2
        ;;
      *[!0-9]*)
        _reject_missing_round "$ROUND"
        exit 2
        ;;
      [1-9]*)
        ;;
      *)
        # All-digit but not led by a nonzero digit: "0", "00", "000", etc.
        _reject_missing_round "$ROUND"
        exit 2
        ;;
    esac
    if [ "${#ROUND}" -gt "$_LEDGER_ROUND_MAX_DIGITS" ]; then
      printf 'review-ledger.sh: --round exceeds %d digits (got %d) — a branch-scoped round counter should never need this many.\n' "$_LEDGER_ROUND_MAX_DIGITS" "${#ROUND}" >&2
      exit 2
    fi

    case "$DISPOSITION" in
      ADDRESS|DEFER)
        [ -n "$FINDING" ] || { printf 'review-ledger.sh: --finding is required for --disposition ADDRESS|DEFER\n' >&2; exit 2; }
        [ -n "$RATIONALE" ] || { printf 'review-ledger.sh: --rationale is required for --disposition ADDRESS|DEFER\n' >&2; exit 2; }
        ;;
      CLEAN)
        [ -z "$FINDING" ] || { printf 'review-ledger.sh: --finding must be omitted for --disposition CLEAN\n' >&2; exit 2; }
        [ -z "$RATIONALE" ] || { printf 'review-ledger.sh: --rationale must be omitted for --disposition CLEAN\n' >&2; exit 2; }
        [ "$SOURCE" = "n/a" ] || { printf 'review-ledger.sh: --source must be omitted for --disposition CLEAN\n' >&2; exit 2; }
        ;;
      *)
        printf "review-ledger.sh: --disposition must be ADDRESS, DEFER, or CLEAN, got '%s'\n" "$DISPOSITION" >&2
        exit 2
        ;;
    esac
    # Absent (empty) never aborts -- only a present-but-invalid value does.
    # This is consistent with --disposition above but optional rather than
    # required.
    case "$AUTHORING_AGENT" in
      ""|code-writer|inline|mixed|unknown) ;;
      *) printf "review-ledger.sh: --authoring-agent must be one of code-writer, inline, mixed, unknown, got '%s'\n" "$AUTHORING_AGENT" >&2; exit 2 ;;
    esac
    case "$AUTHORING_EFFORT" in
      ""|low|medium|high|xhigh) ;;
      *) printf "review-ledger.sh: --authoring-effort must be one of low, medium, high, xhigh, got '%s'\n" "$AUTHORING_EFFORT" >&2; exit 2 ;;
    esac

    # Reject over-cap fields rather than truncate — silent truncation would
    # corrupt exactly the narrative fidelity this ledger exists to preserve.
    if [ "${#FINDING}" -gt "$_LEDGER_FINDING_MAX_CHARS" ]; then
      printf 'review-ledger.sh: --finding exceeds %d characters (got %d) — shorten it rather than truncate narrative fidelity.\n' "$_LEDGER_FINDING_MAX_CHARS" "${#FINDING}" >&2
      exit 2
    fi
    if [ "${#RATIONALE}" -gt "$_LEDGER_RATIONALE_MAX_CHARS" ]; then
      printf 'review-ledger.sh: --rationale exceeds %d characters (got %d) — shorten it rather than truncate narrative fidelity.\n' "$_LEDGER_RATIONALE_MAX_CHARS" "${#RATIONALE}" >&2
      exit 2
    fi
    if [ "${#SOURCE}" -gt "$_LEDGER_SOURCE_MAX_CHARS" ]; then
      printf 'review-ledger.sh: --source exceeds %d characters (got %d) — shorten it rather than truncate narrative fidelity.\n' "$_LEDGER_SOURCE_MAX_CHARS" "${#SOURCE}" >&2
      exit 2
    fi

    SESSION_ID=$(_resolve_session_id) || exit 2
    REPO_ROOT=$(_resolve_repo_root) || exit 2
    _resolve_ledger_location "$REPO_ROOT" "$SESSION_ID" || exit 2

    if ! mkdir -p "$LEDGER_DIR" 2>/dev/null; then
      printf 'review-ledger.sh: could not create the ledger directory %s. Abort without writing.\n' "$LEDGER_DIR" >&2
      exit 2
    fi
    LOCK_FILE="$LEDGER_FILE.lock"
    if [ "$LEDGER_SCOPE" = "session" ]; then
      printf 'review-ledger.sh: row is session-scoped (HEAD is detached or is the default branch), not shared with the branch ledger: %s\n' "$LEDGER_FILE" >&2
    fi

    # Captured once here, before the dedup check inside
    # _lib_append_json_line_locked, and never recomputed on a lock retry.
    # event_time varies on every call, so it is excluded from the dedup key
    # filter below rather than baked into LINE per attempt.
    # event_time is for a human reading `review-ledger.sh show` to
    # reconstruct the review's own narrative timeline. `show` also sorts on
    # it, to interleave rows from the branch and session files chronologically.
    EVENT_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    # jq -nc avoids hand-escaping free text -- this repo's convention for
    # untrusted/free-form strings. -c keeps each record on one line. The
    # byte-length check after the build keeps the line plus newline within
    # one write(2) (see _LEDGER_LINE_MAX_BYTES), so the O_APPEND write below
    # does not interleave with another session's append to the same file.
    # shellcheck disable=SC2016 # single-quoted on purpose: $finding etc. are
    # jq's own --arg-bound variables, meant to expand inside jq, not bash.
    LINE=$(_lib_jq -nc --arg finding "$FINDING" --arg disposition "$DISPOSITION" \
      --arg rationale "$RATIONALE" --arg source "$SOURCE" \
      --arg authoring_agent "$AUTHORING_AGENT" --arg authoring_effort "$AUTHORING_EFFORT" \
      --arg session_id "$SESSION_ID" \
      --argjson round "$ROUND" --argjson schema_version "$_LEDGER_SCHEMA_VERSION" --arg event_time "$EVENT_TIME" \
      '{schema_version: $schema_version, round: $round, finding: $finding, disposition: $disposition,
        rationale: $rationale, source: $source, authoring_agent: $authoring_agent,
        authoring_effort: $authoring_effort, session_id: $session_id, event_time: $event_time}')
    if [ -z "$LINE" ]; then
      printf 'review-ledger.sh: could not build the ledger line (jq missing, failed, or timed out). Abort without writing.\n' >&2
      exit 2
    fi
    # wc -c counts bytes in any locale, unlike ${#LINE}. jq escapes a control
    # character with no two-character form as six bytes (\u0001), so the
    # per-field character caps above do not bound the built line.
    LINE_BYTES=$(printf '%s' "$LINE" | wc -c | tr -d '[:space:]')
    if ! _is_uint "$LINE_BYTES"; then
      printf 'review-ledger.sh: could not measure the ledger line length (wc missing or failed). Abort without writing.\n' >&2
      exit 2
    fi
    if [ "$LINE_BYTES" -gt "$_LEDGER_LINE_MAX_BYTES" ]; then
      printf 'review-ledger.sh: the ledger line is %d bytes, over the %d-byte limit — shorten --finding, --rationale, and --source (control and non-ASCII characters take more bytes than they count as characters). Abort without writing.\n' "$LINE_BYTES" "$_LEDGER_LINE_MAX_BYTES" >&2
      exit 2
    fi

    # Dedup key excludes schema_version and event_time so two rounds raising
    # an identical finding both land as separate rows. It includes session_id,
    # so two sessions on one branch file each keep their own copy.
    # Each append makes two independently-capped _lib_jq calls: one to build
    # LINE and one for this dedup check. The retention sweep below passes a
    # fixed floor rather than resolving one dynamically, so it adds no third.
    # An environment with neither timeout nor gtimeout on PATH therefore has
    # two uncapped-hang points per append, not one.
    # The primitive returns nonzero only when the row was neither written nor
    # deduplicated, so the row is lost unless the caller retries.
    _lib_append_json_line_locked "$LEDGER_FILE" "$LOCK_FILE" "$LINE" \
      '{round, finding, disposition, rationale, source, authoring_agent, authoring_effort, session_id}'
    APPEND_STATUS=$?
    if [ "$APPEND_STATUS" -ne 0 ]; then
      printf 'review-ledger.sh: could not write the ledger row to %s. The row was not recorded.\n' "$LEDGER_FILE" >&2
      exit 2
    fi

    # Best-effort retention sweep on every append — see _sweep_stale_ledger_files.
    _sweep_stale_ledger_files "$LEDGER_DIR" "$_LEDGER_SWEEP_FLOOR_DAYS" 0 0
    ;;
  show)
    if [ $# -gt 0 ]; then
      usage
      exit 2
    fi
    SESSION_ID=$(_resolve_session_id) || exit 2
    REPO_ROOT=$(_resolve_repo_root) || exit 2
    _resolve_ledger_location "$REPO_ROOT" "$SESSION_ID" || exit 2
    # This worktree's own session file is read alongside the resolved file:
    # in branch scope it holds rows appended while HEAD was detached, and
    # also any appended on the default branch. In session scope it is the
    # resolved file itself, so it is listed once.
    SESSION_LEDGER_FILE=$(_lib_review_ledger_session_path "$CONFIG_DIR" "$REPO_ROOT" "$SESSION_ID") || {
      printf 'review-ledger.sh: could not resolve the session ledger file for this repository. Abort.\n' >&2
      exit 2
    }
    LEDGER_FILES=("$LEDGER_FILE")
    if [ "$SESSION_LEDGER_FILE" != "$LEDGER_FILE" ]; then
      LEDGER_FILES+=("$SESSION_LEDGER_FILE")
    fi
    NONEMPTY_LEDGER_FILES=()
    for f in "${LEDGER_FILES[@]}"; do
      [ -s "$f" ] && NONEMPTY_LEDGER_FILES+=("$f")
    done
    if [ "${#NONEMPTY_LEDGER_FILES[@]}" -eq 0 ]; then
      printf 'review-ledger.sh: no ledger for this %s (%s).\n' "$LEDGER_SCOPE" "$LEDGER_FILE"
      printf 'review-ledger.sh: show scope=%s rows=0 oldest=- newest=- max_round=0 files=\n' "$LEDGER_SCOPE" >&2
      exit 0
    fi
    # Sorted by event_time so rows from the branch and session files
    # interleave chronologically instead of concatenating in file order. A
    # row with no event_time (a pre-existing schema-v1 row, if any) sorts
    # first via the `// ""` fallback, since empty string sorts before any
    # real timestamp.
    # sort_by is stable in practice (verified against jq 1.7) for rows that
    # tie on event_time, but this repo doesn't pin a jq version, so a future
    # jq isn't guaranteed to preserve that tie-break order.
    # Each row is also tagged with its own source ledger file's repo-hash
    # prefix, display-time only. append's own write schema never carries
    # this field. Each file is decoded by its own jq run, because jq's
    # `-R` line reader joins a file's unterminated last line to the next
    # file's first line, which would drop a real row behind a torn tail.
    # `-R`/`fromjson?` parses one raw line at a time so a torn or undecodable
    # line is skipped, matching the append dedup and the Python reader. A
    # non-object line is skipped too, since it cannot carry a round.
    SHOW_STATUS=0
    TAGGED_ROWS=""
    for f in "${NONEMPTY_LEDGER_FILES[@]}"; do
      FILE_ROWS=$(_lib_jq -R -n -c '
        inputs | fromjson? | select(type == "object")
        | . + {source_repo_hash: (input_filename | split("/")[-1] | split(".")[0])}
      ' -- "$f") || {
        SHOW_STATUS=1
        break
      }
      [ -n "$FILE_ROWS" ] && TAGGED_ROWS+="$FILE_ROWS"$'\n'
    done
    if [ "$SHOW_STATUS" -eq 0 ]; then
      SHOW_OUTPUT=$(printf '%s' "$TAGGED_ROWS" | _lib_jq -n -r '[inputs] | sort_by(.event_time // "") | .[] | tojson')
      SHOW_STATUS=$?
    fi
    SHOW_FILES_LABEL=$(IFS=,; printf '%s' "${NONEMPTY_LEDGER_FILES[*]}")
    if [ "$SHOW_STATUS" -ne 0 ]; then
      printf 'review-ledger.sh: could not sort ledger rows for display (jq missing, failed, or timed out) -- printing unsorted with no header; the round count is unknown, not 0.\n' >&2
      cat -- "${NONEMPTY_LEDGER_FILES[@]}"
      exit 1
    fi
    # Row count, oldest and newest event_time, and max round across the
    # printed rows; "-" and 0 stand in when no row carries the field.
    SHOW_STATS=$(printf '%s\n' "$SHOW_OUTPUT" | _lib_jq -r -s '
      [length,
       ([.[].event_time // empty | select(. != "")] | min // "-"),
       ([.[].event_time // empty | select(. != "")] | max // "-"),
       ([.[].round // empty | select(type == "number")] | max // 0)] | @tsv
    ')
    SHOW_STATS_STATUS=$?
    IFS=$'\t' read -r SHOW_ROW_COUNT SHOW_OLDEST SHOW_NEWEST SHOW_MAX_ROUND <<<"$SHOW_STATS"
    if [ "$SHOW_STATS_STATUS" -ne 0 ] || ! _is_uint "$SHOW_ROW_COUNT" || ! _is_uint "$SHOW_MAX_ROUND" \
        || [ -z "$SHOW_OLDEST" ] || [ -z "$SHOW_NEWEST" ]; then
      printf 'review-ledger.sh: could not summarize ledger rows for display (jq missing, failed, or timed out) -- printing rows with no header; the round count is unknown, not 0.\n' >&2
      printf '%s\n' "$SHOW_OUTPUT"
      exit 1
    fi
    printf 'review-ledger.sh: show scope=%s rows=%s oldest=%s newest=%s max_round=%s files=%s\n' \
      "$LEDGER_SCOPE" "$SHOW_ROW_COUNT" "$SHOW_OLDEST" "$SHOW_NEWEST" "$SHOW_MAX_ROUND" "$SHOW_FILES_LABEL" >&2
    printf '%s\n' "$SHOW_OUTPUT"
    ;;
  clear-stale)
    DRY_RUN=0
    if [ "${1:-}" = "--dry-run" ]; then
      DRY_RUN=1
      shift
    fi
    if [ $# -gt 0 ]; then
      usage
      exit 2
    fi
    WINDOW_DAYS=$(_ledger_sweep_window_days "$CONFIG_DIR/settings.json")
    _sweep_stale_ledger_files "$LEDGER_DIR" "$WINDOW_DAYS" "$DRY_RUN" 1
    ;;
  *)
    printf "review-ledger.sh: unknown subcommand '%s'\n" "$SUBCOMMAND" >&2
    usage
    exit 2
    ;;
esac
