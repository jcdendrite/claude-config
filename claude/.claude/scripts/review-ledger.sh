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
# Small and fixed: this runs synchronously inside /code-review, so the
# worst-case added latency is bounded retries * the sleep below.
_LEDGER_LOCK_RETRIES=5

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-ledger.sh <subcommand> [args]

Subcommands:
  append code-review --finding <text> --disposition ADDRESS|DEFER \
      --rationale <text> [--source <file:line>]
             Append one finding-disposition event to this session's ledger.
             No-ops (exit 0) if the identical line already exists, or if
             ~/.claude/.review-narrative-ledger-disabled is present.
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

# _sweep_stale_ledger_files LEDGER_DIR DRY_RUN REPORT
# Removes (or, if DRY_RUN=1, reports without removing) every *.jsonl and
# *.lock file under LEDGER_DIR older than 30 days by mtime, across every
# repo-hash — mirrors nudge-handoff-near-context-cap.sh's directory-wide
# `find ... -mtime +30 -delete` sweep of .handoff-nudge-fired.d. REPORT=1
# prints per-file and summary lines (clear-stale); REPORT=0 is silent (the
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

# _append_ledger_line_locked LEDGER_FILE LOCK_FILE LINE
# Acquires a same-directory noclobber lock (bash `set -o noclobber`, the
# idiom _lib_worktree_collision_guard already establishes in this repo)
# around the check-then-append critical section: no-ops if LINE already
# exists verbatim in LEDGER_FILE, else appends it. The lock file's content is
# the holder's PID; a lock whose PID is dead is evicted and retried
# immediately, the same PID-liveness eviction _lib_active_bypass_marker_live
# (_lib.sh) uses for its own markers, rather than waiting out every retry
# against a crashed holder. Falls through to an unlocked append after
# _LEDGER_LOCK_RETRIES failed acquisitions rather than blocking — a duplicate
# line from a lost race is a low-consequence outcome (an inflated count in a
# summary), not data loss. The lock is released via an EXIT trap, so it
# clears whether the append succeeds or fails — this is the only trap this
# script sets.
_append_ledger_line_locked() {
  local ledger_file="$1" line="$3"
  # Deliberately not `local`: the EXIT trap below evaluates $_LEDGER_LOCK_PATH
  # lazily at script-exit time, after this function has already returned and
  # any `local` binding of the same name would be out of scope.
  _LEDGER_LOCK_PATH="$2"
  local attempt=0 stored_pid
  while [ "$attempt" -lt "$_LEDGER_LOCK_RETRIES" ]; do
    if (set -o noclobber; printf '%s\n' "$$" > "$_LEDGER_LOCK_PATH") 2>/dev/null; then
      trap 'rm -f "$_LEDGER_LOCK_PATH"' EXIT
      break
    fi
    stored_pid=$(cat "$_LEDGER_LOCK_PATH" 2>/dev/null | tr -d '[:space:]')
    if [[ "$stored_pid" =~ ^[0-9]+$ ]] && ! kill -0 "$stored_pid" 2>/dev/null; then
      # Dead holder: evict now and retry acquisition on the very next
      # iteration, with no sleep -- this is what makes eviction prompt
      # rather than waiting out the remaining retries.
      rm -f "$_LEDGER_LOCK_PATH" 2>/dev/null
      attempt=$((attempt + 1))
      continue
    fi
    attempt=$((attempt + 1))
    sleep 0.05
  done
  if [ -f "$ledger_file" ] && grep -qFx -e "$line" -- "$ledger_file" 2>/dev/null; then
    # A dedup no-op must still count as activity on this session's own
    # ledger file, or a long-running session's later no-op append leaves a
    # stale mtime for the directory-wide sweep below to delete out from
    # under it.
    touch -- "$ledger_file" 2>/dev/null
    return 0
  fi
  printf '%s\n' "$line" >> "$ledger_file"
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
    while [ $# -gt 0 ]; do
      case "$1" in
        --finding|--disposition|--rationale|--source)
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
        *)
          printf "review-ledger.sh: unknown argument '%s'\n" "$1" >&2
          usage
          exit 2
          ;;
      esac
    done

    [ -n "$FINDING" ] || { printf 'review-ledger.sh: --finding is required\n' >&2; exit 2; }
    case "$DISPOSITION" in
      ADDRESS|DEFER) ;;
      *) printf "review-ledger.sh: --disposition must be ADDRESS or DEFER, got '%s'\n" "$DISPOSITION" >&2; exit 2 ;;
    esac
    [ -n "$RATIONALE" ] || { printf 'review-ledger.sh: --rationale is required\n' >&2; exit 2; }

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

    # jq -nc rather than hand-escaping free text: already this repo's
    # convention for untrusted/free-form strings. -c keeps each record on
    # one line, well under PIPE_BUF, so the O_APPEND write below is atomic.
    # shellcheck disable=SC2016 # single-quoted on purpose: $finding etc. are
    # jq's own --arg-bound variables, meant to expand inside jq, not bash.
    LINE=$(_lib_jq -nc --arg finding "$FINDING" --arg disposition "$DISPOSITION" \
      --arg rationale "$RATIONALE" --arg source "$SOURCE" \
      '{finding: $finding, disposition: $disposition, rationale: $rationale, source: $source}')
    if [ -z "$LINE" ]; then
      printf 'review-ledger.sh: could not build the ledger line (jq missing, failed, or timed out). Abort without writing.\n' >&2
      exit 2
    fi

    _append_ledger_line_locked "$LEDGER_FILE" "$LOCK_FILE" "$LINE"

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
