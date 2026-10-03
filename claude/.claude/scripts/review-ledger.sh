#!/bin/bash
# Append-only review-narrative ledger: /code-review appends one line per
# finding-disposition event as it runs, so a mid-review compaction or
# session resume doesn't lose which findings were raised, how they were
# dispositioned, and why.
# Usage: review-ledger.sh <append|show|render|clear-stale> [args]
# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

set -u

# Stow links a new file into an existing directory only when ./install.sh re-runs.
# The script runs with `set -u` only, so an unchecked failed `.` would continue.
# shellcheck source=_review-ledger-lib.sh
. "$(dirname "$0")/_review-ledger-lib.sh" || {
  printf 'review-ledger.sh: could not load _review-ledger-lib.sh beside this script. Run ./install.sh from the claude-config checkout to link it.\n' >&2
  exit 2
}

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
_LEDGER_SCHEMA_VERSION=4

usage() {
  cat >&2 <<'EOF'
Usage: ~/.claude/scripts/review-ledger.sh <subcommand> [args]

Subcommands:
  append code-review --disposition ADDRESS|DEFER|SETTLED|CLEAN --round <N> \
      [--finding <text> --rationale <text>] [--source <path>[:<start>[-<end>]]] \
      [--decided-by engineer|plan-architect|carry] [--engineer-quote <text>] \
      [--enforcement-invariant] [--carry-forward] [--defer-criterion <name>] \
      [--ref <id>] [--cited-line <path>:<line>[-<line>]] \
      [--authoring-agent code-writer|inline|mixed|unknown] \
      [--authoring-effort low|medium|high|xhigh]
             Append one finding-disposition event to this branch's ledger
             (this session's ledger on a detached HEAD or the default branch).
             --finding/--rationale are required for ADDRESS|DEFER|SETTLED;
             --finding/--rationale/--source must be omitted for CLEAN.
             --round is the 1-based review round number for this
             /code-review run on this branch.
             DEFER and SETTLED require --source as <repo-relative path> with an
             optional :<start>[-<end>] line range (1 to 9 digits, no leading
             zero, start <= end). A path-only source never carries. An
             absolute path under the repo is stored repo-relative, and a
             leading './' is dropped. Any other absolute path, and a '..',
             '.' or empty segment, is rejected. A range is hashed
             from the working tree (site_hash), and a missing file, a range
             past the end of file, or blank-only text is rejected.
             DEFER requires --defer-criterion, one of:
             orthogonal-scope, coordinated-multi-pr-effort,
             gold-plating-beyond-declared-user-surface,
             contract-pinned-at-another-layer, edge-case-below-current-scale.
             SETTLED requires --decided-by. engineer requires --engineer-quote
             (verbatim, at most 200 characters, never truncated). plan-architect
             takes no quote. --engineer-quote is accepted only there.
             --enforcement-invariant (engineer SETTLED only) labels a decision
             that is asked again on every repeat. --carry-forward (engineer
             SETTLED with a range-form source, never with the label) lets a
             later repeat carry the decision. Neither flag is accepted elsewhere.
             --ref <id> links a row to one earlier live decision (a DEFER or
             SETTLED row that is not a carry). ADDRESS closes it. A DEFER or
             SETTLED supersedes it. An engineer decision is superseded only by
             ADDRESS or an engineer SETTLED, which keeps the label of an
             invariant decision. CLEAN takes no --ref.
             A carry is --decided-by carry (on DEFER or SETTLED) with --ref, a
             range-form --source, --cited-line and --rationale, and never a
             quote, label or --carry-forward. It is accepted only for a live
             DEFER decision with a range-form source, or a live engineer SETTLED
             logged --carry-forward, and only when --cited-line lies inside the
             carry's range and that range hashes to the decision's site_hash. A
             DEFER carry restates the decision's --defer-criterion. A carry
             prints one line naming the decision and how to reopen it. An
             engineer SETTLED prints its stored quote. When jq fails to clean
             the text, the quote (on a carry line, the finding) reads `(not
             shown: jq could not clean it)`. The row was recorded either way;
             show prints the text once jq can clean it. Stored text is verbatim.
             These two lines and show print it with control and format
             characters (zero-width, bidi, line separators) shown as spaces.
             A --ref is checked against this branch's ledger file only (this
             session's file in session scope).
             No-ops (exit 0) if the identical line (by round, finding,
             disposition, rationale, source, authoring_agent,
             authoring_effort, session_id and every field above) already exists.
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
             line is skipped and not counted. If the read or sort fails, show
             prints no rows and no header, and if only the summary fails it
             prints the cleaned rows without a header. Both exit 1, and the
             round count is then unknown, not 0.
  render [--pr-json <file|->] [--out <path>]
             Print the PR-body block built from the live decisions in the
             resolved ledger file (this branch's, or this session's in session
             scope). Unlike show, it reads no other session's file. Without
             --pr-json the output is the block alone, empty when no decision is
             live. --pr-json takes the JSON of `gh pr view --json body` (a file,
             or - for stdin) and returns the whole body with the block replaced,
             appended when absent, and dropped when it would hold no rows; every
             byte outside the block stays identical, and a row whose last cell
             is not a ledger id is kept. With --out the result goes to <path>,
             which must be a file directly under <repo>/agent-reviews/ named
             review-ledger-<suffix>.md or pr-body-<suffix>.md (a relative path
             is taken from the working directory) and is deleted
             first, then written atomically only on success. stdout reads
             `changed: <path>` or `unchanged` (no file written). Exits 2 on a
             rejected --out, and 1 with no file on empty input, an unreadable
             --pr-json file, a failed ledger read, an unpaired or repeated
             delimiter in the body, or a stale <path> that cannot be deleted.
  clear-stale [--dry-run]
             Remove ledger (.jsonl) and orphaned lock (.lock) files older
             than the resolved sweep window (Claude Code's cleanupPeriodDays
             setting, floored at 30 days), across every repo-hash.
             --dry-run reports without removing.

Exit status: append exits 2 on every refusal, a rejected call and an
environment failure alike, so its message carries the distinction. show and
render exit 2 for a rejected call or an unresolved ledger location, and 1 when
a read, sort or write fails. Two cases differ: a symlinked or non-regular
ledger file exits 2 from show and 1 from render, and render exits 1 when it
cannot delete a stale --out, before it resolves the ledger location.
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
  # shellcheck disable=SC2016 # single-quoted for literal display text: escaping each $ inside double quotes is the alternative, and it would obscure a message that only names the env vars $HOME and $CLAUDE_CONFIG_DIR.
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
    DECIDED_BY=""
    ENGINEER_QUOTE=""
    # "1" when the boolean flag was given, "" otherwise.
    ENFORCEMENT_INVARIANT=""
    CARRY_FORWARD=""
    DEFER_CRITERION=""
    REF=""
    CITED_LINE=""
    # Flags given with an empty value. The variables above read the same for an
    # empty value and an omitted flag, so presence is tracked here.
    EMPTY_VALUE_FLAGS=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --finding|--disposition|--rationale|--source|--round|--authoring-agent|--authoring-effort|--decided-by|--engineer-quote|--defer-criterion|--ref|--cited-line)
          if [ $# -lt 2 ]; then
            printf "review-ledger.sh: %s requires a value\n" "$1" >&2
            exit 2
          fi
          ;;
      esac
      case "$1" in
        --decided-by|--engineer-quote|--defer-criterion|--ref|--cited-line)
          [ -n "$2" ] || EMPTY_VALUE_FLAGS="${EMPTY_VALUE_FLAGS:+$EMPTY_VALUE_FLAGS }$1"
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
        --decided-by) DECIDED_BY="$2"; shift 2 ;;
        --engineer-quote) ENGINEER_QUOTE="$2"; shift 2 ;;
        --enforcement-invariant) ENFORCEMENT_INVARIANT=1; shift ;;
        --carry-forward) CARRY_FORWARD=1; shift ;;
        --defer-criterion) DEFER_CRITERION="$2"; shift 2 ;;
        --ref) REF="$2"; shift 2 ;;
        --cited-line) CITED_LINE="$2"; shift 2 ;;
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
      ADDRESS|DEFER|SETTLED)
        [ -n "$FINDING" ] || { printf 'review-ledger.sh: --finding is required for --disposition ADDRESS|DEFER|SETTLED\n' >&2; exit 2; }
        [ -n "$RATIONALE" ] || { printf 'review-ledger.sh: --rationale is required for --disposition ADDRESS|DEFER|SETTLED\n' >&2; exit 2; }
        ;;
      CLEAN)
        [ -z "$FINDING" ] || { printf 'review-ledger.sh: --finding must be omitted for --disposition CLEAN\n' >&2; exit 2; }
        [ -z "$RATIONALE" ] || { printf 'review-ledger.sh: --rationale must be omitted for --disposition CLEAN\n' >&2; exit 2; }
        [ "$SOURCE" = "n/a" ] || { printf 'review-ledger.sh: --source must be omitted for --disposition CLEAN\n' >&2; exit 2; }
        ;;
      *)
        printf "review-ledger.sh: --disposition must be ADDRESS, DEFER, SETTLED, or CLEAN, got '%s'\n" "$DISPOSITION" >&2
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

    if [ "${#CITED_LINE}" -gt "$_LEDGER_SOURCE_MAX_CHARS" ]; then
      printf 'review-ledger.sh: --cited-line exceeds %d characters (got %d) — shorten it rather than truncate narrative fidelity.\n' "$_LEDGER_SOURCE_MAX_CHARS" "${#CITED_LINE}" >&2
      exit 2
    fi
    _review_ledger_validate_flags "$DISPOSITION" "$DECIDED_BY" "$ENGINEER_QUOTE" "$ENFORCEMENT_INVARIANT" \
      "$CARRY_FORWARD" "$DEFER_CRITERION" "$REF" "$CITED_LINE" "$SOURCE" "$EMPTY_VALUE_FLAGS" || exit 2

    SESSION_ID=$(_resolve_session_id) || exit 2
    REPO_ROOT=$(_resolve_repo_root) || exit 2
    _resolve_ledger_location "$REPO_ROOT" "$SESSION_ID" || exit 2
    LOCK_FILE="$LEDGER_FILE.lock"
    # Checked before the first read of the ledger.
    _review_ledger_check_regular_file "$LEDGER_FILE" && _review_ledger_check_regular_file "$LOCK_FILE" || exit 2

    # A decision's source is normalized to a repo-relative path. A range-form
    # source gets a site hash, and a carry must reproduce its decision's hash.
    SITE_HASH=""
    if [ "$DISPOSITION" = DEFER ] || [ "$DISPOSITION" = SETTLED ]; then
      SOURCE=$(_review_ledger_normalize_location "$REPO_ROOT" "$SOURCE") || exit 2
      SOURCE_PARTS=$(_review_ledger_location_parts "$SOURCE")
      if [ -z "$SOURCE_PARTS" ] && { [ "$DECIDED_BY" = carry ] || [ -n "$CARRY_FORWARD" ]; }; then
        printf 'review-ledger.sh: a carry, and a --carry-forward decision, need a range-form --source (<path>:<start>[-<end>]), got %s\n' "$SOURCE" >&2
        exit 2
      fi
    fi
    if [ -n "$CITED_LINE" ]; then
      CITED_LINE=$(_review_ledger_normalize_location "$REPO_ROOT" "$CITED_LINE") || exit 2
    fi
    if [ -n "$REF" ]; then
      # The trailing 1 defers a retired --ref to _review_ledger_check_not_retired below, which needs the built row.
      _review_ledger_check_ref "$LEDGER_SCOPE" "$LEDGER_FILE" "$DISPOSITION" "$DECIDED_BY" "$ENFORCEMENT_INVARIANT" "$REF" 1 || exit 2
    fi
    if [ "$DECIDED_BY" = carry ]; then
      SITE_HASH=$(_review_ledger_check_carry "$REPO_ROOT" "$DISPOSITION" "$DEFER_CRITERION" "$SOURCE" "$CITED_LINE" "$_REVIEW_LEDGER_REF_INFO") || exit 2
    elif [ -n "${SOURCE_PARTS:-}" ]; then
      IFS=$'\t' read -r SITE_PATH SITE_START SITE_END <<<"$SOURCE_PARTS"
      SITE_HASH=$(_review_ledger_site_hash "$REPO_ROOT" "$SITE_PATH" "$SITE_START" "$SITE_END") || exit 2
    fi

    # Ledger rows hold verbatim engineer quotes, so the directory is created 0700 and each new file 0600.
    umask 077
    if ! mkdir -p "$LEDGER_DIR" 2>/dev/null; then
      printf 'review-ledger.sh: could not create the ledger directory %s. Abort without writing.\n' "$LEDGER_DIR" >&2
      exit 2
    fi
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
    INVARIANT_JSON=false
    [ -n "$ENFORCEMENT_INVARIANT" ] && INVARIANT_JSON=true
    CARRY_FORWARD_JSON=false
    [ -n "$CARRY_FORWARD" ] && CARRY_FORWARD_JSON=true
    # shellcheck disable=SC2016 # single-quoted on purpose: $finding etc. are
    # jq's own --arg-bound variables, and double-quoting would expand them in
    # the shell before jq sees them.
    LINE=$(_lib_jq -nc --arg finding "$FINDING" --arg disposition "$DISPOSITION" \
      --arg rationale "$RATIONALE" --arg source "$SOURCE" \
      --arg authoring_agent "$AUTHORING_AGENT" --arg authoring_effort "$AUTHORING_EFFORT" \
      --arg session_id "$SESSION_ID" --arg decided_by "$DECIDED_BY" --arg engineer_quote "$ENGINEER_QUOTE" \
      --argjson enforcement_invariant "$INVARIANT_JSON" --argjson carry_forward "$CARRY_FORWARD_JSON" \
      --arg defer_criterion "$DEFER_CRITERION" --arg ref "$REF" --arg cited_line "$CITED_LINE" \
      --arg site_hash "$SITE_HASH" \
      --argjson round "$ROUND" --argjson schema_version "$_LEDGER_SCHEMA_VERSION" --arg event_time "$EVENT_TIME" \
      '{schema_version: $schema_version, round: $round, finding: $finding, disposition: $disposition,
        rationale: $rationale, source: $source, authoring_agent: $authoring_agent,
        authoring_effort: $authoring_effort, session_id: $session_id, decided_by: $decided_by,
        engineer_quote: $engineer_quote, enforcement_invariant: $enforcement_invariant,
        carry_forward: $carry_forward, defer_criterion: $defer_criterion, ref: $ref,
        cited_line: $cited_line, site_hash: $site_hash, event_time: $event_time}')
    if [ -z "$LINE" ]; then
      printf 'review-ledger.sh: could not build the ledger line (jq missing, failed, or timed out). Abort without writing.\n' >&2
      exit 2
    fi
    # The id is the hash of the row as built, appended as its last field. jq -c
    # output always ends in }, and the id is hex, so it needs no escaping.
    ROW_ID=$(_review_ledger_row_id "$LINE") || {
      printf 'review-ledger.sh: could not compute the row id (sha256sum failed). Abort without writing.\n' >&2
      exit 2
    }
    LINE="${LINE%\}},\"id\":\"${ROW_ID}\"}"
    # The splice is string surgery, and every reader skips a row jq cannot parse,
    # so the row is parsed back with its id before it is written.
    # shellcheck disable=SC2016 # single-quoted on purpose: $id is jq's own --arg-bound variable, and double-quoting would expand it in the shell before jq sees it.
    if ! printf '%s\n' "$LINE" | _lib_jq -e --arg id "$ROW_ID" 'type == "object" and .id == $id' >/dev/null 2>&1; then
      printf 'review-ledger.sh: the built ledger row did not parse back with its id (jq failed or timed out, or the id splice misexpanded). The row was not recorded.\n' >&2
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
      printf 'review-ledger.sh: the ledger line is %d bytes, over the %d-byte limit — shorten --finding, --rationale, --source, --cited-line, and --engineer-quote (control and non-ASCII characters take more bytes than they count as characters). Abort without writing.\n' "$LINE_BYTES" "$_LEDGER_LINE_MAX_BYTES" >&2
      exit 2
    fi

    # A --ref the check above found retired is accepted only as a retry of the
    # retirer's own write, which the append below then dedups.
    _review_ledger_check_not_retired "$LEDGER_FILE" "$LINE" "$REF" || exit 2

    # The dedup key excludes schema_version, event_time and id, so two rounds
    # raising an identical finding both land as separate rows.
    # The dedup key includes session_id, so two sessions on one branch file each
    # keep their own copy.

    # Call tally: the jq, awk and git calls that can run without a timeout,
    # which are the points where an append can hang.
    # Hashing is an in-memory pipe that cannot hang, so the sha256sum and awk
    # pairs that only hash are left out.
    # Each append makes capped _lib_jq calls to build LINE, to parse it back and
    # for the dedup check. An engineer SETTLED and a carry add one more to clean
    # the text they print.
    # The retention sweep passes a fixed floor rather than resolving one
    # dynamically, so it adds no further call.
    # A --ref adds capped _lib_jq passes over the resolved file (read, then look
    # up), and a retired --ref adds a read and a compare. A --ref naming a carry
    # repeats the read and look-up once for its rejection advice.
    # A range-form source adds one capped awk read for the site hash.
    # _resolve_ledger_location adds capped git calls: symbolic-ref HEAD, the
    # origin/HEAD resolution, and the candidate rev-parse probes.
    # The bare git rev-parse --show-toplevel and the sweep's find are uncapped.
    # With neither timeout nor gtimeout on PATH, each capped call above is an
    # uncapped-hang point.

    # The primitive returns nonzero only when the row was neither written nor
    # deduplicated, so the row is lost unless the caller retries.
    _lib_append_json_line_locked "$LEDGER_FILE" "$LOCK_FILE" "$LINE" \
      '{round, finding, disposition, rationale, source, authoring_agent, authoring_effort, session_id, decided_by, engineer_quote, enforcement_invariant, carry_forward, defer_criterion, ref, cited_line, site_hash}'
    APPEND_STATUS=$?
    if [ "$APPEND_STATUS" -ne 0 ]; then
      printf 'review-ledger.sh: could not write the ledger row to %s. The row was not recorded.\n' "$LEDGER_FILE" >&2
      exit 2
    fi

    if [ "$DECIDED_BY" = carry ]; then
      _review_ledger_carry_line "$REF" "$FINDING" "$_REVIEW_LEDGER_REF_INFO"
    elif [ "$DISPOSITION" = SETTLED ] && [ "$DECIDED_BY" = engineer ]; then
      if [ -n "$ENFORCEMENT_INVARIANT" ]; then
        STORED_QUOTE_NOTE="enforcement-invariant: asked again on every repeat"
      elif [ -n "$CARRY_FORWARD" ]; then
        STORED_QUOTE_NOTE="carry-forward: yes"
      else
        STORED_QUOTE_NOTE="carry-forward: no"
      fi
      if SHOWN_QUOTE=$(_review_ledger_clean_text "$ENGINEER_QUOTE"); then
        printf 'review-ledger.sh: stored engineer quote: "%s" (%s)\n' "$SHOWN_QUOTE" "$STORED_QUOTE_NOTE"
      else
        printf 'review-ledger.sh: stored engineer quote: (not shown: jq could not clean it) (%s)\n' "$STORED_QUOTE_NOTE"
      fi
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
      _review_ledger_check_regular_file "$f" || exit 2
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
    # Each row is tagged with its own source ledger file's repo-hash prefix,
    # display-time only: append's own write schema never carries this field.
    # _review_ledger_read_rows decodes one raw line at a time, so a torn or
    # undecodable line, or one that is not an object, is skipped rather than
    # failing the read.
    SHOW_STATUS=0
    TAGGED_ROWS=$(_review_ledger_read_rows "${NONEMPTY_LEDGER_FILES[@]}") || SHOW_STATUS=1
    if [ "$SHOW_STATUS" -eq 0 ]; then
      SHOW_OUTPUT=$(printf '%s\n' "$TAGGED_ROWS" | _lib_jq -n -r "$_REVIEW_LEDGER_JQ_COMMON"'[inputs] | sort_by(.event_time // "") | .[] | tojson | clean')
      SHOW_STATUS=$?
    fi
    SHOW_FILES_LABEL=$(IFS=,; printf '%s' "${NONEMPTY_LEDGER_FILES[*]}")
    if [ "$SHOW_STATUS" -ne 0 ]; then
      printf 'review-ledger.sh: could not show ledger rows because jq failed (missing, failed, or timed out), so no rows or header are printed; the round count is unknown, not 0.\n' >&2
      exit 1
    fi
    # Row count, oldest and newest event_time, and max round across the
    # printed rows; "-" and 0 stand in when no row carries a well-formed
    # event_time or a round.
    # shellcheck disable=SC2016 # single-quoted on purpose: $times is jq's own variable, and double-quoting would expand it in the shell before jq sees it.
    SHOW_STATS=$(printf '%s\n' "$SHOW_OUTPUT" | _lib_jq -r -s "$_REVIEW_LEDGER_JQ_COMMON"'
      [.[] | .event_time | event_time_text | select(. != "")] as $times
      | [length,
         ($times | min // "-"),
         ($times | max // "-"),
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
  render)
    RENDER_PR_JSON=""
    RENDER_OUT=""
    while [ $# -gt 0 ]; do
      case "$1" in
        --pr-json|--out)
          if [ $# -lt 2 ]; then
            printf "review-ledger.sh: %s requires a value\n" "$1" >&2
            exit 2
          fi
          ;;
      esac
      case "$1" in
        --pr-json) RENDER_PR_JSON="$2"; shift 2 ;;
        --out) RENDER_OUT="$2"; shift 2 ;;
        *)
          printf "review-ledger.sh: unknown argument '%s'\n" "$1" >&2
          usage
          exit 2
          ;;
      esac
    done
    REPO_ROOT=$(_resolve_repo_root) || exit 2
    # A stale --out is deleted before any later step that can exit, so a failed
    # render never leaves the previous round's body file to be published.
    if [ -n "$RENDER_OUT" ]; then
      _review_ledger_check_out "$REPO_ROOT" "$RENDER_OUT" "$RENDER_PR_JSON" "$LEDGER_DIR" || exit 2
      _review_ledger_remove_out "$RENDER_OUT" || exit 1
    fi
    SESSION_ID=$(_resolve_session_id) || exit 2
    _resolve_ledger_location "$REPO_ROOT" "$SESSION_ID" || exit 2
    # Only the resolved file is read, unlike show, which also merges this
    # worktree's session file: a session-file row is not part of the branch's
    # decisions and must not reach a PR body.
    _review_ledger_render "$LEDGER_FILE" "$RENDER_PR_JSON" "$RENDER_OUT" || exit 1
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
