#!/bin/bash
# Crash-resilience checkpoint store for review-orchestrator: one append-only
# JSONL file per orchestrator run, keyed by <repo-hash>.<orchestrator_run_id>,
# so a killed run can be re-dispatched with the same orchestrator_run_id and
# resume from its last recorded step instead of restarting.
# Usage: orchestrator-checkpoint.sh <append|read> <orchestrator_run_id> [args]
# Entries are bounded to step id, status, and marker-hash only — never raw
# findings or diff text — so this store never becomes a second durable copy
# of exactly the content review-orchestrator exists to keep out of a
# long-lived context.
# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

set -u

# ${#VAR} below counts codepoints under a UTF-8 locale but bytes under
# C/POSIX -- these caps are not pinned to either, mirroring review-ledger.sh's
# own field caps.
_CHECKPOINT_STEP_MAX_CHARS=200
_CHECKPOINT_STATUS_MAX_CHARS=100
_CHECKPOINT_MARKER_HASH_MAX_CHARS=128
# Small and fixed: this runs synchronously inside review-orchestrator's own
# turn, mirroring review-ledger.sh's _LEDGER_LOCK_RETRIES rationale.
_CHECKPOINT_LOCK_RETRIES=5

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/orchestrator-checkpoint.sh <subcommand> <orchestrator_run_id> [args]

Subcommands:
  append <orchestrator_run_id> --step <id> --status <text> [--marker-hash <hash>]
             Append one checkpoint entry for this run. No-ops (exit 0) if the
             identical line already exists. Conventional --status values are
             "started" (a step began) and "done" (a step completed) — a
             resumed run should retry any step whose last entry is "started"
             with no later "done" entry for the same --step, and skip any
             step whose last entry is "done".
  read <orchestrator_run_id>
             Print this run's checkpoint contents, or an absence message.
EOF
}

_validate_run_id() {
  local run_id="$1"
  # Reuses the same safe-path-component check marker.sh/review-ledger.sh
  # apply to a session id -- orchestrator_run_id is equally caller-supplied
  # (minted by the dispatching parent, named in review-orchestrator's own
  # dispatch prompt) and equally gets concatenated into a filesystem path
  # below, so an id containing '..' or '/' must not escape the checkpoint
  # directory.
  if ! _lib_valid_session_id_component "$run_id"; then
    printf 'orchestrator-checkpoint.sh: orchestrator_run_id %s is not a valid path component. Abort without writing.\n' "$run_id" >&2
    return 2
  fi
}

_resolve_repo_root() {
  local root
  root=$(git rev-parse --show-toplevel 2>/dev/null | tr -d '\n')
  if [ -z "$root" ]; then
    printf 'orchestrator-checkpoint.sh: not inside a git repository\n' >&2
    return 2
  fi
  printf '%s' "$root"
}

if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
  usage
  exit 0
fi

if [ $# -lt 2 ]; then
  usage
  exit 2
fi

# Fail closed: every path below is built from CONFIG_DIR, so an
# unresolvable CLAUDE_CONFIG_DIR (relative value, or empty $HOME with no
# override) must abort rather than fall through to a root-anchored path.
CONFIG_DIR=$(_lib_config_dir) || {
  # shellcheck disable=SC2016 # single-quoted for literal display text: $HOME
  # and $CLAUDE_CONFIG_DIR name the env vars in the message, not shell expansions.
  printf 'orchestrator-checkpoint.sh: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or $HOME is unset/empty). Abort without writing.\n' >&2
  exit 2
}
CHECKPOINT_DIR="$CONFIG_DIR/orchestrator-checkpoints"

SUBCOMMAND="$1"
shift
RUN_ID="$1"
shift
_validate_run_id "$RUN_ID" || exit 2

REPO_ROOT=$(_resolve_repo_root) || exit 2
REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT")
CHECKPOINT_FILE="$CHECKPOINT_DIR/$REPO_HASH.$RUN_ID.jsonl"
LOCK_FILE="$CHECKPOINT_FILE.lock"

case "$SUBCOMMAND" in
  append)
    STEP=""
    STATUS=""
    MARKER_HASH="n/a"
    while [ $# -gt 0 ]; do
      case "$1" in
        --step|--status|--marker-hash)
          if [ $# -lt 2 ]; then
            printf "orchestrator-checkpoint.sh: %s requires a value\n" "$1" >&2
            exit 2
          fi
          ;;
      esac
      case "$1" in
        --step) STEP="$2"; shift 2 ;;
        --status) STATUS="$2"; shift 2 ;;
        --marker-hash) MARKER_HASH="$2"; shift 2 ;;
        *)
          printf "orchestrator-checkpoint.sh: unknown argument '%s'\n" "$1" >&2
          usage
          exit 2
          ;;
      esac
    done

    [ -n "$STEP" ] || { printf 'orchestrator-checkpoint.sh: --step is required\n' >&2; exit 2; }
    [ -n "$STATUS" ] || { printf 'orchestrator-checkpoint.sh: --status is required\n' >&2; exit 2; }

    # Reject over-cap fields rather than truncate — silent truncation would
    # corrupt exactly the resumability this checkpoint exists to preserve.
    if [ "${#STEP}" -gt "$_CHECKPOINT_STEP_MAX_CHARS" ]; then
      printf 'orchestrator-checkpoint.sh: --step exceeds %d characters (got %d) — a step id should be short and structural, never a quoted finding or diff excerpt.\n' "$_CHECKPOINT_STEP_MAX_CHARS" "${#STEP}" >&2
      exit 2
    fi
    if [ "${#STATUS}" -gt "$_CHECKPOINT_STATUS_MAX_CHARS" ]; then
      printf 'orchestrator-checkpoint.sh: --status exceeds %d characters (got %d).\n' "$_CHECKPOINT_STATUS_MAX_CHARS" "${#STATUS}" >&2
      exit 2
    fi
    if [ "${#MARKER_HASH}" -gt "$_CHECKPOINT_MARKER_HASH_MAX_CHARS" ]; then
      printf 'orchestrator-checkpoint.sh: --marker-hash exceeds %d characters (got %d).\n' "$_CHECKPOINT_MARKER_HASH_MAX_CHARS" "${#MARKER_HASH}" >&2
      exit 2
    fi

    if ! mkdir -p "$CHECKPOINT_DIR" 2>/dev/null; then
      printf 'orchestrator-checkpoint.sh: could not create the checkpoint directory %s. Abort without writing.\n' "$CHECKPOINT_DIR" >&2
      exit 2
    fi

    # jq -nc rather than hand-escaping free text: already this repo's
    # convention for untrusted/free-form strings (review-ledger.sh's own
    # append does the same). -c keeps each record on one line, well under
    # PIPE_BUF, so the O_APPEND write is atomic.
    # shellcheck disable=SC2016 # single-quoted on purpose: $step etc. are
    # jq's own --arg-bound variables, meant to expand inside jq, not bash.
    LINE=$(_lib_jq -nc --arg step "$STEP" --arg status "$STATUS" --arg marker_hash "$MARKER_HASH" \
      '{step: $step, status: $status, marker_hash: $marker_hash}')
    if [ -z "$LINE" ]; then
      printf 'orchestrator-checkpoint.sh: could not build the checkpoint line (jq missing, failed, or timed out). Abort without writing.\n' >&2
      exit 2
    fi

    _lib_append_line_locked "$CHECKPOINT_FILE" "$LOCK_FILE" "$LINE" "$_CHECKPOINT_LOCK_RETRIES"

    # Best-effort retention sweep on every append, mirroring
    # review-ledger.sh's own use of the same shared helper.
    _lib_sweep_stale_files "$CHECKPOINT_DIR" 0 0
    ;;
  read)
    if [ $# -gt 0 ]; then
      usage
      exit 2
    fi
    if [ ! -s "$CHECKPOINT_FILE" ]; then
      printf 'orchestrator-checkpoint.sh: no checkpoint for run %s (%s).\n' "$RUN_ID" "$CHECKPOINT_FILE"
      exit 0
    fi
    cat -- "$CHECKPOINT_FILE"
    ;;
  *)
    printf "orchestrator-checkpoint.sh: unknown subcommand '%s'\n" "$SUBCOMMAND" >&2
    usage
    exit 2
    ;;
esac
