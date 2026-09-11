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
# 4 digits (max round 9999) is generous for a session-scoped invocation counter.
_LEDGER_ROUND_MAX_DIGITS=4

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-ledger.sh <subcommand> [args]

Subcommands:
  append code-review --disposition ADDRESS|DEFER|CLEAN --round <N> \
      [--finding <text> --rationale <text>] [--source <file:line>] \
      [--authoring-agent code-writer|inline|mixed|unknown] \
      [--authoring-effort low|medium|high|xhigh]
             Append one finding-disposition event to this session's ledger.
             --finding/--rationale are required for ADDRESS|DEFER;
             --finding/--rationale/--source must be omitted for CLEAN.
             --round is the 1-based review round number for this
             /code-review run in this session.
             No-ops (exit 0) if the identical line (by round, finding,
             disposition, rationale, source, authoring_agent,
             authoring_effort) already exists, or if
             ~/.claude/.review-narrative-ledger-disabled is present.
             --authoring-agent and --authoring-effort are optional; an
             absent flag never aborts the append, but an invalid value does.
  show       Print this session's ledger contents, or an absence message.
  clear-stale [--dry-run]
             Remove ledger (.jsonl) and orphaned lock (.lock) files older
             than 30 days, across every repo-hash. --dry-run reports
             without removing.
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

--round is the 1-based review round number for *this* /code-review run in
this session: 1 for the first round, incrementing once per subsequent
/code-review invocation you've made. Retry with --round added:

  ~/.claude/scripts/review-ledger.sh append code-review --finding "<summary>" \
    --disposition ADDRESS|DEFER --rationale "<one line>" --round <N> \
    --authoring-agent code-writer|inline|mixed|unknown --authoring-effort high

Required so two rounds raising an identical finding don't collapse into one
ledger line under this script's round-scoped dedup key. Abort without writing.
EOF
}

# _sweep_stale_ledger_files LEDGER_DIR DRY_RUN REPORT
# Removes (or, if DRY_RUN=1, reports without removing) every *.jsonl and
# *.lock file under LEDGER_DIR older than 30 days by mtime, across every
# repo-hash — mirrors nudge-handoff-near-context-cap.sh's directory-wide
# `find ... -mtime +30 -delete` sweep of .handoff-nudge-fired.d. REPORT=1
# prints per-file and summary lines (clear-stale). REPORT=0 is silent (the
# best-effort sweep append performs on every invocation).
_sweep_stale_ledger_files() {
  local ledger_dir="$1" dry_run="$2" report="$3"
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
  done < <(find "$ledger_dir" -maxdepth 1 \( -name '*.jsonl' -o -name '*.lock' \) -mtime +30 -print0 2>/dev/null)
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

    # Kill switch: no-op without touching the ledger at all. `show` still
    # reads whatever was already written — the switch gates new writes only.
    if [ -f "$CONFIG_DIR/.review-narrative-ledger-disabled" ]; then
      exit 0
    fi

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
      printf 'review-ledger.sh: --round exceeds %d digits (got %d) — a session-scoped round counter should never need this many.\n' "$_LEDGER_ROUND_MAX_DIGITS" "${#ROUND}" >&2
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
    REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")

    if ! mkdir -p "$LEDGER_DIR" 2>/dev/null; then
      printf 'review-ledger.sh: could not create the ledger directory %s. Abort without writing.\n' "$LEDGER_DIR" >&2
      exit 2
    fi
    LEDGER_FILE="$LEDGER_DIR/$REPO_HASH.$SESSION_ID.jsonl"
    LOCK_FILE="$LEDGER_FILE.lock"

    # Captured once here, before the dedup check inside
    # _lib_append_json_line_locked, and never recomputed on a lock retry.
    # event_time varies on every call, so it is excluded from the dedup key
    # filter below rather than baked into LINE per attempt.
    # event_time is for a human reading `review-ledger.sh show` to
    # reconstruct the review's own narrative timeline. No code reader
    # branches on it.
    EVENT_TIME=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    # jq -nc (one record per line) for an atomic O_APPEND write below. See
    # docs/transcript-analysis-architecture.md's ledger append-lock section
    # for why and its local-filesystem-only caveat.
    # shellcheck disable=SC2016 # single-quoted on purpose: $finding etc. are
    # jq's own --arg-bound variables, meant to expand inside jq, not bash.
    # schema_version carries no reader that branches on it today. It's for
    # a future migration to distinguish row shapes without re-deriving them
    # from which optional keys are present or absent.
    LINE=$(_lib_jq -nc --arg finding "$FINDING" --arg disposition "$DISPOSITION" \
      --arg rationale "$RATIONALE" --arg source "$SOURCE" \
      --arg authoring_agent "$AUTHORING_AGENT" --arg authoring_effort "$AUTHORING_EFFORT" \
      --argjson round "$ROUND" --argjson schema_version 2 --arg event_time "$EVENT_TIME" \
      '{schema_version: $schema_version, round: $round, finding: $finding, disposition: $disposition,
        rationale: $rationale, source: $source, authoring_agent: $authoring_agent,
        authoring_effort: $authoring_effort, event_time: $event_time}')
    if [ -z "$LINE" ]; then
      printf 'review-ledger.sh: could not build the ledger line (jq missing, failed, or timed out). Abort without writing.\n' >&2
      exit 2
    fi

    # Dedup key excludes schema_version (a constant) and event_time (varies
    # on every call, including an otherwise-identical retry). Two rounds
    # raising a textually identical finding must both land. Whole-line
    # dedup would otherwise collapse them to one line now that event_time
    # differs per call.
    _lib_append_json_line_locked "$LEDGER_FILE" "$LOCK_FILE" "$LINE" \
      '{round, finding, disposition, rationale, source, authoring_agent, authoring_effort}'

    # Best-effort retention sweep on every append — see _sweep_stale_ledger_files.
    _sweep_stale_ledger_files "$LEDGER_DIR" 0 0
    ;;
  show)
    if [ $# -gt 0 ]; then
      usage
      exit 2
    fi
    SESSION_ID=$(_resolve_session_id) || exit 2
    REPO_ROOT=$(_resolve_repo_root) || exit 2
    REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
    LEDGER_FILE="$LEDGER_DIR/$REPO_HASH.$SESSION_ID.jsonl"
    if [ ! -s "$LEDGER_FILE" ]; then
      printf 'review-ledger.sh: no ledger for this session (%s).\n' "$LEDGER_FILE"
      exit 0
    fi
    cat -- "$LEDGER_FILE"
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
    _sweep_stale_ledger_files "$LEDGER_DIR" "$DRY_RUN" 1
    ;;
  *)
    printf "review-ledger.sh: unknown subcommand '%s'\n" "$SUBCOMMAND" >&2
    usage
    exit 2
    ;;
esac
