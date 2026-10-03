#!/bin/bash
# _review-ledger-lib.sh — validation, site hash, row reader and render for review-ledger.sh.
#
# Sourced by review-ledger.sh after hooks/_lib.sh, whose _lib_jq, _lib_capped and
# _lib_hash_diff_text it calls. Not executable on its own; source it, do not invoke it.
# It sets no EXIT trap, because _lib_acquire_append_lock owns the sourcing script's one.

_REVIEW_LEDGER_DELIM_START='<!-- code-review:deferred:start -->'
_REVIEW_LEDGER_DELIM_END='<!-- code-review:deferred:end -->'

# The five DEFER criteria, named after the closed list in code-review/SKILL.md.
_REVIEW_LEDGER_DEFER_CRITERIA=(
  orthogonal-scope
  coordinated-multi-pr-effort
  gold-plating-beyond-declared-user-surface
  contract-pinned-at-another-layer
  edge-case-below-current-scale
)

# Length of a row's id and of a site hash, in hex digits. 12 digits stay below
# the PR-body redaction gate's 32-digit hex-run detector.
_REVIEW_LEDGER_HEX_LENGTH=12
_REVIEW_LEDGER_QUOTE_MAX_CHARS=200

# Field separator of _review_ledger_lookup_ref's output line. A non-whitespace
# IFS character keeps empty fields from collapsing in `read`.
_REVIEW_LEDGER_FIELD_SEP=$'\x1f'

# Set by _review_ledger_check_ref: the lookup line of the row --ref names.
# shellcheck disable=SC2034 # read by review-ledger.sh, which sources this lib, not by the lib itself. A stdout return was not used, because a command substitution would set it in a subshell the caller cannot read.
_REVIEW_LEDGER_REF_INFO=""
# Set by _review_ledger_check_ref when a non-carry row already retired the row
# --ref names: that retirer's id and disposition.
_REVIEW_LEDGER_REF_RETIRED_BY=""
_REVIEW_LEDGER_REF_RETIRED_DISP=""

_review_ledger_reject() {
  printf 'review-ledger.sh: %s\n' "$1" >&2
  return 1
}

# Appended to every DEFER rejection, so the message alone tells a caller how to
# correct its call.
_review_ledger_defer_hint() {
  local names
  names=$(IFS=,; printf '%s' "${_REVIEW_LEDGER_DEFER_CRITERIA[*]}")
  printf 'DEFER needs --source <repo-relative path>[:<start>[-<end>]] and --defer-criterion, one of: %s' "${names//,/, }"
}

_review_ledger_is_criterion() {
  local candidate="$1" known
  for known in "${_REVIEW_LEDGER_DEFER_CRITERIA[@]}"; do
    [ "$candidate" = "$known" ] && return 0
  done
  return 1
}

# _review_ledger_is_row_id VALUE: true iff VALUE is exactly _REVIEW_LEDGER_HEX_LENGTH lowercase hex digits.
# The class lists its digits instead of using ranges, because a collating locale
# without ASCII ranges would otherwise let uppercase letters through.
_review_ledger_is_row_id() {
  case "$1" in
    *[!0123456789abcdef]*) return 1 ;;
  esac
  [ "${#1}" -eq "$_REVIEW_LEDGER_HEX_LENGTH" ]
}

# _review_ledger_validate_flags DISPOSITION DECIDED_BY QUOTE INVARIANT CARRY_FORWARD CRITERION REF CITED_LINE SOURCE [EMPTY_VALUE_FLAGS]
# Flag-shape rules only, with no I/O. INVARIANT and CARRY_FORWARD are "1" when
# the flag was given. An absent SOURCE is "n/a". EMPTY_VALUE_FLAGS lists, space
# separated, each value flag the caller received with an empty value, since an
# empty value is otherwise indistinguishable from an omitted flag. Prints the
# reason to stderr and returns 1 on the first violation.
_review_ledger_validate_flags() {
  local disposition="$1" decided_by="$2" quote="$3" invariant="$4" carry_forward="$5"
  local criterion="$6" ref="$7" cited_line="$8" source="$9" empty_value_flags="${10:-}"
  local is_engineer_settled=0 is_carry=0
  [ "$decided_by" = carry ] && is_carry=1
  [ "$disposition" = SETTLED ] && [ "$decided_by" = engineer ] && is_engineer_settled=1

  if [ -n "$empty_value_flags" ]; then
    _review_ledger_reject "${empty_value_flags%% *} was given an empty value; pass a value or omit the flag"
    return 1
  fi

  case "$disposition" in
    ADDRESS|CLEAN)
      [ -z "$decided_by" ] || { _review_ledger_reject "--decided-by is only accepted on SETTLED, or as carry on DEFER (got it on $disposition)"; return 1; }
      ;;
    DEFER)
      case "$decided_by" in
        ''|carry) ;;
        *) _review_ledger_reject "--disposition DEFER accepts only --decided-by carry, got '$decided_by'"; return 1 ;;
      esac
      ;;
    SETTLED)
      case "$decided_by" in
        engineer|plan-architect|carry) ;;
        '') _review_ledger_reject "--decided-by engineer|plan-architect|carry is required for --disposition SETTLED"; return 1 ;;
        *) _review_ledger_reject "--decided-by must be engineer, plan-architect, or carry, got '$decided_by'"; return 1 ;;
      esac
      ;;
    *)
      _review_ledger_reject "--disposition must be ADDRESS, DEFER, SETTLED, or CLEAN, got '$disposition'"
      return 1
      ;;
  esac

  if [ "$is_engineer_settled" -eq 1 ]; then
    case "$quote" in
      *[![:space:]]*) ;;
      *) _review_ledger_reject "--engineer-quote is required, and must not be empty or whitespace, for --decided-by engineer"; return 1 ;;
    esac
    if [ "${#quote}" -gt "$_REVIEW_LEDGER_QUOTE_MAX_CHARS" ]; then
      _review_ledger_reject "--engineer-quote exceeds $_REVIEW_LEDGER_QUOTE_MAX_CHARS characters (got ${#quote}) — quote a shorter excerpt that keeps its qualifying clauses, or ask the engineer for a shorter statement."
      return 1
    fi
  elif [ -n "$quote" ]; then
    _review_ledger_reject "--engineer-quote is only accepted with --disposition SETTLED --decided-by engineer"
    return 1
  fi

  if [ "$invariant" = 1 ] && [ "$is_engineer_settled" -ne 1 ]; then
    _review_ledger_reject "--enforcement-invariant is only accepted with --disposition SETTLED --decided-by engineer"
    return 1
  fi
  if [ "$carry_forward" = 1 ]; then
    if [ "$is_engineer_settled" -ne 1 ]; then
      _review_ledger_reject "--carry-forward is only accepted with --disposition SETTLED --decided-by engineer"
      return 1
    fi
    if [ "$invariant" = 1 ]; then
      _review_ledger_reject "--carry-forward cannot be combined with --enforcement-invariant: an invariant decision is asked again on every repeat"
      return 1
    fi
  fi

  if [ "$disposition" = DEFER ]; then
    if ! _review_ledger_is_criterion "$criterion"; then
      _review_ledger_reject "--defer-criterion '$criterion' is missing or not a known criterion. $(_review_ledger_defer_hint)"
      return 1
    fi
  elif [ -n "$criterion" ]; then
    _review_ledger_reject "--defer-criterion is only accepted with --disposition DEFER"
    return 1
  fi

  if [ "$disposition" = CLEAN ] && [ -n "$ref" ]; then
    _review_ledger_reject "--ref must be omitted for --disposition CLEAN"
    return 1
  fi
  if [ -n "$ref" ] && ! _review_ledger_is_row_id "$ref"; then
    _review_ledger_reject "--ref '$ref' is not a ledger row id ($_REVIEW_LEDGER_HEX_LENGTH lowercase hex digits)"
    return 1
  fi
  if [ "$is_carry" -eq 1 ]; then
    [ -n "$ref" ] || { _review_ledger_reject "--decided-by carry requires --ref <decision id>"; return 1; }
    [ -n "$cited_line" ] || { _review_ledger_reject "--decided-by carry requires --cited-line <path>:<line>[-<line>]"; return 1; }
  elif [ -n "$cited_line" ]; then
    _review_ledger_reject "--cited-line is only accepted with --decided-by carry"
    return 1
  fi

  case "$disposition" in
    DEFER|SETTLED)
      if [ -z "$source" ] || [ "$source" = "n/a" ]; then
        if [ "$disposition" = DEFER ]; then
          _review_ledger_reject "--source is required for --disposition DEFER. $(_review_ledger_defer_hint)"
        else
          _review_ledger_reject "--source <repo-relative path>[:<start>[-<end>]] is required for --disposition SETTLED"
        fi
        return 1
      fi
      ;;
  esac
  return 0
}

# _review_ledger_repo_relative_path REPO_ROOT SPEC PATH
# Prints PATH repo-relative: an absolute path under REPO_ROOT loses that prefix,
# and a leading './' is dropped. SPEC, the caller's whole argument, names it in
# messages. Rejects any other absolute path, a '..', '.' or empty segment, a
# control character, and an empty path, because the site hash and --out read or
# write the file and one site needs one spelling. Returns 1 with the reason on
# stderr.
_review_ledger_repo_relative_path() {
  local repo_root="$1" spec="$2" path="$3"
  case "$path" in
    *[[:cntrl:]]*)
      _review_ledger_reject "'${spec//[[:cntrl:]]/ }' has a control character (a tab or newline, for one) in its path"
      return 1
      ;;
  esac
  if [ "${path#/}" != "$path" ]; then
    if [ "${path#"$repo_root"/}" = "$path" ]; then
      _review_ledger_reject "'$spec' is an absolute path outside the repository; use a repo-relative path"
      return 1
    fi
    path="${path#"$repo_root"/}"
  fi
  while [ "${path#./}" != "$path" ]; do
    path="${path#./}"
  done
  case "/$path/" in
    *//*|*/../*)
      _review_ledger_reject "'$spec' has an empty or '..' path segment; use a repo-relative path"
      return 1
      ;;
    */./*)
      _review_ledger_reject "'$spec' has a '.' path segment; drop it so the path has one spelling"
      return 1
      ;;
  esac
  [ -n "$path" ] || { _review_ledger_reject "'$spec' has no path"; return 1; }
  printf '%s' "$path"
}

# _review_ledger_normalize_location REPO_ROOT SPEC
# Validates SPEC against <path>[:<start>[-<end>]] and prints it with a
# repo-relative path (see _review_ledger_repo_relative_path). <start> and <end>
# are 1 to 9 digits with no leading zero, and <start> <= <end>. A SPEC
# containing ':' must end in such a range.
# Prints the reason to stderr and returns 1 on rejection.
_review_ledger_normalize_location() {
  local repo_root="$1" spec="$2"
  local path range start="" end=""
  local range_pattern='^([1-9][0-9]{0,8})(-([1-9][0-9]{0,8}))?$'
  case "$spec" in
    *:*)
      path="${spec%:*}"
      range="${spec##*:}"
      if [[ ! "$range" =~ $range_pattern ]]; then
        _review_ledger_reject "'$spec' does not end in a line range <start>[-<end>] of 1 to 9 digits with no leading zero"
        return 1
      fi
      start="${BASH_REMATCH[1]}"
      end="${BASH_REMATCH[3]:-}"
      if [ -n "$end" ] && [ "$start" -gt "$end" ]; then
        _review_ledger_reject "'$spec' has a range start after its end"
        return 1
      fi
      ;;
    *)
      path="$spec"
      range=""
      ;;
  esac
  path=$(_review_ledger_repo_relative_path "$repo_root" "$spec" "$path") || return 1
  if [ -n "$range" ]; then
    printf '%s:%s' "$path" "$range"
  else
    printf '%s' "$path"
  fi
}

# _review_ledger_location_parts CANONICAL_SPEC
# Prints "<path>", "<start>" and "<end>" tab-separated for a range-form spec
# from _review_ledger_normalize_location (end equals start for a single line).
# Prints nothing and returns 1 for a path-only spec.
_review_ledger_location_parts() {
  local spec="$1" path range start end
  case "$spec" in
    *:*) ;;
    *) return 1 ;;
  esac
  path="${spec%:*}"
  range="${spec##*:}"
  start="${range%%-*}"
  end="${range#*-}"
  printf '%s\t%s\t%s' "$path" "$start" "$end"
}

# _review_ledger_physical_file FILE
# Prints FILE's physical path, following symlinks on the final component and on
# every directory above it. Returns 1 when a directory is missing or the links
# loop.
_review_ledger_physical_file() {
  local target="$1" hops=0 link dir
  while [ -L "$target" ]; do
    hops=$((hops + 1))
    [ "$hops" -le 40 ] || return 1
    link=$(readlink -- "$target") || return 1
    case "$link" in
      /*) target="$link" ;;
      *) target="$(dirname -- "$target")/$link" ;;
    esac
  done
  dir=$(cd -P -- "$(dirname -- "$target")" 2>/dev/null && pwd -P) || return 1
  printf '%s/%s' "$dir" "$(basename -- "$target")"
}

# _review_ledger_site_hash REPO_ROOT PATH START END
# Prints the first _REVIEW_LEDGER_HEX_LENGTH hex digits of the hash of lines
# START through END of REPO_ROOT/PATH, read from the working tree. Rejects a
# missing file, a file whose physical path lies outside REPO_ROOT (a symlink
# out of the repository), a range past the end of the file, and blank-only text.
# One capped awk read prints the lines: awk counts an unterminated last line,
# and exits 3 when the file has fewer than END lines. Command substitution
# strips trailing newlines, so a newline added at end of file is not an edit.
_review_ledger_site_hash() {
  local repo_root="$1" path="$2" range_start="$3" range_end="$4"
  local file="$repo_root/$path" text digest awk_status=0 real_root real_file
  if [ ! -f "$file" ]; then
    _review_ledger_reject "source file '$path' does not exist in the working tree, so its text cannot be hashed"
    return 1
  fi
  real_root=$(cd -P -- "$repo_root" 2>/dev/null && pwd -P)
  real_file=$(_review_ledger_physical_file "$file")
  if [ -z "$real_root" ] || [ -z "$real_file" ]; then
    _review_ledger_reject "could not resolve the physical path of source file '$path'"
    return 1
  fi
  case "$real_file" in
    "$real_root"/?*) ;;
    *)
      _review_ledger_reject "source file '$path' resolves outside the repository through a symlink; name a file that lives in the repository"
      return 1
      ;;
  esac
  text=$(_lib_capped awk -v range_start="$range_start" -v range_end="$range_end" \
    'NR >= range_start { print } NR == range_end { found = 1; exit } END { if (!found) exit 3 }' \
    "$file") || awk_status=$?
  if [ "$awk_status" -eq 3 ]; then
    _review_ledger_reject "'$path' has fewer than $range_end lines, so the range $range_start-$range_end is past the end of the file"
    return 1
  fi
  if [ "$awk_status" -ne 0 ]; then
    _review_ledger_reject "could not read '$path' to hash lines $range_start-$range_end (awk failed or timed out)"
    return 1
  fi
  case "$text" in
    *[![:space:]]*) ;;
    *)
      _review_ledger_reject "lines $range_start-$range_end of '$path' hold only whitespace; name the block the decision is about"
      return 1
      ;;
  esac
  digest=$(_lib_hash_diff_text "$text") || {
    _review_ledger_reject "could not hash lines $range_start-$range_end of '$path' (sha256sum failed)"
    return 1
  }
  printf '%s' "${digest:0:$_REVIEW_LEDGER_HEX_LENGTH}"
}

# _review_ledger_row_id LINE
# Prints the first _REVIEW_LEDGER_HEX_LENGTH hex digits of LINE's sha256.
_review_ledger_row_id() {
  local digest
  digest=$(_lib_hash_diff_text "$1") || return 1
  printf '%s' "${digest:0:$_REVIEW_LEDGER_HEX_LENGTH}"
}

# _review_ledger_check_regular_file FILE
# Returns 1 with the reason on stderr when FILE is a symlink, dangling or not,
# or exists as anything but a regular file. An absent FILE passes. Ledger and
# lock files are only ever created by review-ledger.sh as regular files, so a
# symlink there redirects the read or the append to another path.
# The check is a point-in-time test, not an atomic no-follow open, and it does
# not detect a hard link, which is a regular file.
_review_ledger_check_regular_file() {
  local file="$1"
  if [ -L "$file" ] || { [ -e "$file" ] && [ ! -f "$file" ]; }; then
    _review_ledger_reject "refusing '$file': a ledger or lock path must be a regular file, not a symlink or another file type. Do not remove it. Stop and report this path to the engineer, because an object planted there may be evidence of a write that bypassed the review-state hook"
    return 1
  fi
}

# _review_ledger_read_rows FILE...
# Prints each file's object rows, one compact JSON object per line, each tagged
# with source_repo_hash (the filename's repo-hash prefix, for display only).
# A line that is not valid JSON, or not an object, is skipped rather than
# failing the read. Each file is read by its own jq run, because jq's raw-line
# reader would join one file's unterminated last line to the next file's first.
# An absent or empty file yields no rows. Returns 1 when jq fails, or when a
# file is a symlink or not a regular file.
_review_ledger_read_rows() {
  local file
  for file in "$@"; do
    _review_ledger_check_regular_file "$file" || return 1
    [ -s "$file" ] || continue
    _lib_jq -R -n -c '
      inputs | fromjson? | select(type == "object")
      | . + {source_repo_hash: (input_filename | split("/")[-1] | split(".")[0])}
    ' -- "$file" || return 1
  done
}

# jq definitions shared by the --ref lookup and render. A row is a "decision"
# when it is a DEFER or SETTLED row with an id that is not a carry. A decision
# is live unless a non-carry row names its id in `ref`.
# `clean` is the one cleaning step for stored text that reaches a reader:
# control, format (bidi, zero-width) and line or paragraph separator characters
# read as spaces.
# `event_time_text` is the one shape check for a stored event_time. It returns
# the writer's own YYYY-MM-DDThh:mm:ssZ form, or "" for any other value, so a
# forged or foreign value never reaches a reader. `event_date` is its date part.
# shellcheck disable=SC2016 # single-quoted on purpose: $rows is jq's own variable, and double-quoting would expand it in the shell before jq sees it.
_REVIEW_LEDGER_JQ_COMMON='
def s: if type == "string" then . else "" end;
def clean: s | gsub("[[:cntrl:]\\p{Cf}\\p{Zl}\\p{Zp}]"; " ");
def event_time_text:
  s | if length == 20 and test("^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z$") then . else "" end;
def event_date: event_time_text | .[0:10];
def flag: if . == true then "1" else "0" end;
def is_carry: (.decided_by | s) == "carry";
def is_decision: ((.disposition | s) == "DEFER" or (.disposition | s) == "SETTLED")
  and ((.id | s) != "") and (is_carry | not);
def retired_index($rows): $rows
  | map(select((is_carry | not) and ((.ref | s) != "")) | {key: (.ref | s), value: true})
  | from_entries;
'

# _review_ledger_clean_text TEXT
# Prints TEXT through the same `clean` step as a published cell, for text echoed
# to the caller. Returns 1 when jq fails.
_review_ledger_clean_text() {
  # shellcheck disable=SC2016 # single-quoted on purpose: $text is jq's own --arg-bound variable, and double-quoting would expand it in the shell before jq sees it.
  _lib_jq -n -j --arg text "$1" "$_REVIEW_LEDGER_JQ_COMMON"'$text | clean'
}

# _review_ledger_lookup_ref LEDGER_FILE REF
# Prints one line of _REVIEW_LEDGER_FIELD_SEP-separated fields describing the
# row whose id is REF: found, is_decision, ref, retired_by, retired_by_disposition,
# disposition, decided_by, enforcement_invariant, carry_forward, site_hash,
# source, defer_criterion, event_date, round, engineer_quote. A row not in the
# file prints only "0". Text fields pass through `clean`, and event_date is the
# stored event_time's date or empty (see `event_date`).
# Returns 1 when the read or the lookup jq fails.
_review_ledger_lookup_ref() {
  local ledger_file="$1" ref="$2" rows
  rows=$(_review_ledger_read_rows "$ledger_file") || return 1
  # shellcheck disable=SC2016 # single-quoted on purpose: $rows and $row are jq's own variables, and double-quoting would expand them in the shell before jq sees them.
  printf '%s\n' "$rows" | _lib_jq -n -r --arg ref "$ref" "$_REVIEW_LEDGER_JQ_COMMON"'
    [inputs] as $rows
    | ([$rows[] | select((.id | s) == $ref)] | .[0]) as $row
    | if $row == null then "0"
      else
        ([$rows[] | select((is_carry | not) and ((.ref | s) == $ref))] | .[0]) as $retirer
        | [ "1",
            ($row | is_decision | flag),
            ($row | .ref | clean),
            ($retirer | .id | clean),
            ($retirer | .disposition | clean),
            ($row | .disposition | clean),
            ($row | .decided_by | clean),
            ($row | .enforcement_invariant | flag),
            ($row | .carry_forward | flag),
            ($row | .site_hash | clean),
            ($row | .source | clean),
            ($row | .defer_criterion | clean),
            ($row | .event_time | event_date),
            ($row | .round | if type == "number" then tostring else "" end),
            ($row | .engineer_quote | clean)
          ] | join("\u001f")
      end
  '
}

# _review_ledger_retirer_pointer RETIRER_ID RETIRER_DISP
# Prints the clause that points a rejected --ref at the row that retired it, and
# nothing otherwise. A DEFER or SETTLED retirer is itself a --ref target, so it
# gets a pointer. An ADDRESS retirer is not one, so the clause would send the
# caller into a second rejection. RETIRER_ID comes from stored text, so it is
# spliced only when it is a row id.
_review_ledger_retirer_pointer() {
  local retirer_id="$1" retirer_disp="$2"
  _review_ledger_is_row_id "$retirer_id" || return 0
  case "$retirer_disp" in
    DEFER | SETTLED) printf '; reference row %s instead if the decision is being revisited' "$retirer_id" ;;
  esac
}

# _review_ledger_carry_ref_advice LEDGER_FILE DECISION_ID
# Prints the tail of the rejection for a --ref that names a carry: the carry's
# decision, and either how to reopen it or which row already retired it. DECISION_ID
# comes from stored text, so nothing is printed unless it is a row id and names a
# decision row in LEDGER_FILE. A retiring row's id is spliced only when it is a row
# id too. A DEFER or SETTLED retirer is itself a --ref target, so it adds a pointer.
_review_ledger_carry_ref_advice() {
  local ledger_file="$1" decision_id="$2" decision_info decision_is_decision decision_retired_by decision_retired_disp
  _review_ledger_is_row_id "$decision_id" || return 0
  decision_info=$(_review_ledger_lookup_ref "$ledger_file" "$decision_id") || return 0
  IFS="$_REVIEW_LEDGER_FIELD_SEP" read -r _ decision_is_decision _ decision_retired_by decision_retired_disp _ \
    <<<"$decision_info"
  [ "$decision_is_decision" = 1 ] || return 0
  if [ -z "$decision_retired_by" ]; then
    printf '; its decision is %s. ADDRESS --ref %s reopens that decision and retires its carries from the PR block too' \
      "$decision_id" "$decision_id"
  elif _review_ledger_is_row_id "$decision_retired_by"; then
    printf '; its decision is %s, which row %s already retired, so it cannot be reopened' "$decision_id" "$decision_retired_by"
    _review_ledger_retirer_pointer "$decision_retired_by" "$decision_retired_disp"
  else
    printf '; its decision is %s, which is already retired, so it cannot be reopened' "$decision_id"
  fi
}

# _review_ledger_check_ref SCOPE_NOUN LEDGER_FILE DISPOSITION DECIDED_BY INVARIANT REF [DEFER_RETIRED]
# Checks the one-hop --ref rules against LEDGER_FILE, the only file read.
# SCOPE_NOUN ("branch" or "session") names the ledger in messages. The row
# REF names must exist, be a decision (not a carry, not an ADDRESS row), and be
# live. A non-carry row may only supersede an engineer decision as an ADDRESS
# or an engineer SETTLED, and an engineer SETTLED successor of an invariant
# decision must carry --enforcement-invariant. A carry must reference a DEFER
# decision with a range-form source, or an engineer SETTLED logged
# --carry-forward, of the carry's own disposition.
# A retired decision is rejected, unless DEFER_RETIRED is 1 and the row is not a
# carry. Then it sets _REVIEW_LEDGER_REF_RETIRED_BY and _DISP and returns 0, so
# that _review_ledger_check_not_retired can accept a retry of the retirer's own
# write once the row is built.
# Sets _REVIEW_LEDGER_REF_INFO to the lookup line for _review_ledger_check_carry
# and _review_ledger_carry_line. Returns 1 with the reason on stderr.
_review_ledger_check_ref() {
  local scope_noun="$1" ledger_file="$2" disposition="$3" decided_by="$4" invariant="$5" ref="$6" defer_retired="${7:-}"
  local info found is_decision ref_of retired_by retired_disp ref_disposition ref_decided_by
  local ref_invariant ref_carry_forward ref_site_hash
  _REVIEW_LEDGER_REF_INFO=""
  _REVIEW_LEDGER_REF_RETIRED_BY=""
  _REVIEW_LEDGER_REF_RETIRED_DISP=""
  info=$(_review_ledger_lookup_ref "$ledger_file" "$ref") || {
    _review_ledger_reject "could not read the ledger to check --ref (jq missing, failed, or timed out). Abort without writing."
    return 1
  }
  IFS="$_REVIEW_LEDGER_FIELD_SEP" read -r found is_decision ref_of retired_by retired_disp \
    ref_disposition ref_decided_by ref_invariant ref_carry_forward ref_site_hash _ <<<"$info"
  if [ "$found" != 1 ]; then
    _review_ledger_reject "--ref $ref is not in this $scope_noun's ledger. Only this scope's resolved file is read, so a decision in a session-scope file (a detached-HEAD or default-branch session, or another session) is not visible here, and a torn line or a swept ledger hides it too. Run 'review-ledger.sh show' to list the ids."
    return 1
  fi
  if [ "$ref_decided_by" = carry ]; then
    _review_ledger_reject "--ref $ref names an orchestrator carry, not a decision$(_review_ledger_carry_ref_advice "$ledger_file" "$ref_of")"
    return 1
  fi
  if [ "$is_decision" != 1 ]; then
    _review_ledger_reject "--ref $ref names a $ref_disposition row, not a DEFER or SETTLED decision"
    return 1
  fi
  if [ -n "$retired_by" ]; then
    if [ "$decided_by" = carry ] || [ "$defer_retired" != 1 ]; then
      _review_ledger_reject "--ref $ref is no longer live: row $retired_by ($retired_disp) retired it$(_review_ledger_retirer_pointer "$retired_by" "$retired_disp")"
      return 1
    fi
    _REVIEW_LEDGER_REF_RETIRED_BY="$retired_by"
    _REVIEW_LEDGER_REF_RETIRED_DISP="$retired_disp"
    _REVIEW_LEDGER_REF_INFO="$info"
    return 0
  fi
  if [ "$decided_by" = carry ]; then
    if [ "$ref_disposition" != "$disposition" ]; then
      _review_ledger_reject "a $disposition carry must reference a $disposition decision, but $ref is $ref_disposition"
      return 1
    fi
    if [ -z "$ref_site_hash" ]; then
      _review_ledger_reject "decision $ref has a path-only source, so it never carries; log a fresh disposition"
      return 1
    fi
    if [ "$ref_disposition" = SETTLED ]; then
      if [ "$ref_decided_by" != engineer ]; then
        _review_ledger_reject "decision $ref was decided by $ref_decided_by, and only an engineer decision logged --carry-forward carries"
        return 1
      fi
      if [ "$ref_carry_forward" != 1 ]; then
        _review_ledger_reject "decision $ref was not logged with --carry-forward (an enforcement-invariant decision never is), so it never carries; take the settled-site stop"
        return 1
      fi
    fi
  elif [ "$ref_disposition" = SETTLED ] && [ "$ref_decided_by" = engineer ]; then
    if [ "$disposition" != ADDRESS ] && { [ "$disposition" != SETTLED ] || [ "$decided_by" != engineer ]; }; then
      _review_ledger_reject "decision $ref is an engineer's: only an ADDRESS or an engineer SETTLED may supersede it, not $disposition${decided_by:+ by $decided_by}"
      return 1
    fi
    if [ "$ref_invariant" = 1 ] && [ "$disposition" = SETTLED ] && [ "$invariant" != 1 ]; then
      _review_ledger_reject "decision $ref is labelled --enforcement-invariant, so an engineer SETTLED that supersedes it must carry that label"
      return 1
    fi
  fi
  _REVIEW_LEDGER_REF_INFO="$info"
}

# _review_ledger_check_not_retired LEDGER_FILE LINE REF
# Rejects the --ref a non-carry row names when _review_ledger_check_ref found it
# retired, unless a non-carry row that names REF holds the same fields as LINE.
# That is a retry of a write that already landed, and the append then dedups it.
# The fields compared are every one except id, schema_version and event_time,
# which is the append's dedup key. Returns 1 with the reason on stderr, and also
# when the ledger cannot be read.
_review_ledger_check_not_retired() {
  local ledger_file="$1" line="$2" ref="$3" is_retry
  [ -n "$_REVIEW_LEDGER_REF_RETIRED_BY" ] || return 0
  # shellcheck disable=SC2016 # single-quoted on purpose: $candidate and $ref are jq's own variables, and double-quoting would expand them in the shell before jq sees them.
  is_retry=$(_review_ledger_read_rows "$ledger_file" | _lib_jq -n -r --argjson candidate "$line" --arg ref "$ref" \
    "$_REVIEW_LEDGER_JQ_COMMON"'
    def dedup_fields: del(.id, .schema_version, .event_time, .source_repo_hash);
    ($candidate | dedup_fields) as $key
    | any(inputs | select((is_carry | not) and ((.ref | s) == $ref)); dedup_fields == $key)
  ') || is_retry=""
  [ "$is_retry" = true ] && return 0
  _review_ledger_reject "--ref $ref is no longer live: row $_REVIEW_LEDGER_REF_RETIRED_BY ($_REVIEW_LEDGER_REF_RETIRED_DISP) retired it$(_review_ledger_retirer_pointer "$_REVIEW_LEDGER_REF_RETIRED_BY" "$_REVIEW_LEDGER_REF_RETIRED_DISP")"
  return 1
}

# _review_ledger_check_carry REPO_ROOT DISPOSITION CRITERION SOURCE CITED_LINE INFO
# Accepts a carry only when its criterion equals its DEFER decision's, its
# cited line lies inside its own range, and the text at that range hashes to the
# decision's site hash. SOURCE and CITED_LINE are canonical range-form specs
# from _review_ledger_normalize_location, and INFO is _REVIEW_LEDGER_REF_INFO.
# Prints the carry's site hash on stdout. Returns 1 with the reason on stderr.
_review_ledger_check_carry() {
  local repo_root="$1" disposition="$2" criterion="$3" source="$4" cited_line="$5" info="$6"
  local ref_site_hash ref_criterion
  local source_parts source_path source_start source_end cited_parts cited_path cited_start cited_end site_hash
  IFS="$_REVIEW_LEDGER_FIELD_SEP" read -r _ _ _ _ _ _ _ _ _ ref_site_hash _ ref_criterion _ <<<"$info"
  if [ "$disposition" = DEFER ] && [ "$criterion" != "$ref_criterion" ]; then
    _review_ledger_reject "--defer-criterion '$criterion' differs from the decision's '$ref_criterion'; re-run the criteria against the current code and log a fresh DEFER if the criterion changed"
    return 1
  fi
  source_parts=$(_review_ledger_location_parts "$source") || { _review_ledger_reject "a carry needs a range-form --source"; return 1; }
  cited_parts=$(_review_ledger_location_parts "$cited_line") || { _review_ledger_reject "--cited-line needs a line number: <path>:<line>[-<line>]"; return 1; }
  IFS=$'\t' read -r source_path source_start source_end <<<"$source_parts"
  IFS=$'\t' read -r cited_path cited_start cited_end <<<"$cited_parts"
  if [ "$cited_path" != "$source_path" ] || [ "$cited_start" -lt "$source_start" ] || [ "$cited_end" -gt "$source_end" ]; then
    _review_ledger_reject "--cited-line $cited_line is not inside the carry's --source $source; name the block that contains the reviewer's cited line"
    return 1
  fi
  site_hash=$(_review_ledger_site_hash "$repo_root" "$source_path" "$source_start" "$source_end") || return 1
  if [ "$site_hash" != "$ref_site_hash" ]; then
    _review_ledger_reject "the text at $source no longer matches the decided block (edited, reflowed, or changed line endings); log a fresh disposition"
    return 1
  fi
  printf '%s' "$site_hash"
}

# _review_ledger_carry_line REF FINDING INFO
# Prints the one line a carry reports: the decision's id, date, round and
# basis (criterion, or the engineer's stored quote), the carried finding, and
# how to reopen. The finding is cleaned for display, so the line stays one line,
# and reads as a placeholder outside quote marks when jq cannot clean it.
# INFO is _REVIEW_LEDGER_REF_INFO.
_review_ledger_carry_line() {
  local ref="$1" finding="$2" info="$3"
  local ref_disposition ref_criterion ref_event_date ref_round ref_quote basis shown_finding
  IFS="$_REVIEW_LEDGER_FIELD_SEP" read -r _ _ _ _ _ ref_disposition _ _ _ _ _ ref_criterion \
    ref_event_date ref_round ref_quote <<<"$info"
  if [ "$ref_disposition" = DEFER ]; then
    basis="DEFER ($ref_criterion)"
  else
    basis="engineer quote: \"$ref_quote\""
  fi
  if shown_finding=$(_review_ledger_clean_text "$finding"); then
    shown_finding="finding \"$shown_finding\""
  else
    shown_finding="a finding (not shown: jq could not clean it)"
  fi
  printf 'review-ledger.sh: carry of decision %s (decided %s, round %s; %s): the orchestrator matched %s to it and the engineer did not see this finding; reopen %s by logging ADDRESS --ref %s, which retires that decision and its carries from the PR block.\n' \
    "$ref" "${ref_event_date:-unknown}" "$ref_round" "$basis" "$shown_finding" "$ref" "$ref"
}

# Static jq program: render the PR-body block, or merge it into a PR body.
# Inputs: ledger rows on stdin (JSON lines), $body (raw PR body), $digest
# (true renders the block alone), $block_start and $block_end (the block delimiters).
# Exit 3 with a message on stderr when the body's delimiters are unpaired or
# repeated. The cell-escape rules are the ones docs/scripts.md describes.
# shellcheck disable=SC2016 # single-quoted on purpose: $rows, $body, $block_start and the rest are jq's own variables, and double-quoting would expand them in the shell before jq sees them.
_REVIEW_LEDGER_JQ_RENDER='
def esc_plain: clean | gsub("\\\\"; "\\\\") | gsub("\\|"; "\\|") | gsub("<!--"; "&lt;!--");
def codespan:
  (clean | gsub("\\|"; "\\|")) as $text
  | ([$text | scan("`+") | length] | max // 0) as $longest_run
  | ("`" * ($longest_run + 1)) as $fence
  | $fence + " " + $text + " " + $fence;
def decided_cell:
  ((.event_time | event_date) + " round " + (.round | if type == "number" then tostring else "?" end)) | esc_plain;
def table_row($cells): "| " + ($cells | join(" | ")) + " |";
def decision_row:
  if (.disposition | s) == "DEFER" then
    table_row([(.finding | esc_plain), (.source | esc_plain), (.defer_criterion | esc_plain),
               (.rationale | esc_plain), decided_cell, (.id | esc_plain)])
  else
    table_row([(.finding | esc_plain), (.source | esc_plain),
               (if (.decided_by | s) == "engineer" then "engineer: " + (.engineer_quote | codespan)
                else (.decided_by | esc_plain) end),
               (.rationale | esc_plain), decided_cell, (.id | esc_plain)])
  end;
def carry_row:
  table_row([(.finding | esc_plain), (.source | esc_plain),
             ("orchestrator-matched carry of " + (.ref | s) | esc_plain),
             (.rationale | esc_plain), decided_cell, (.id | esc_plain)]);
def fence_open:
  ([capture("^ {0,3}(?<run>`{3,}|~{3,})(?<rest>.*)$")] | .[0]) as $m
  | if $m == null then null
    else ($m.run | .[0:1]) as $mark
      | if $mark == "`" and ($m.rest | contains("`")) then null
        else {mark: $mark, length: ($m.run | length)} end
    end;
def fence_close($fence):
  test("^ {0,3}" + $fence.mark + "{" + ($fence.length | tostring) + ",}[ \t]*$");
def is_separator_line: rtrimstr("\r") | test("^\\s*\\|(\\s*:?-+:?\\s*\\|)+\\s*$");
def is_row_line: rtrimstr("\r") | test("^\\s*\\|.*\\|\\s*$");
def last_cell:
  (rtrimstr("\r") | sub("[ \t]+$"; "") | rtrimstr("|")) as $inner
  | ([$inner | match("(?<!\\\\)\\|"; "g") | .offset] | last) as $last_pipe
  | (if $last_pipe == null then $inner else $inner[$last_pipe + 1:] end)
  | sub("^\\s+"; "") | sub("\\s+$"; "");

[inputs] as $rows
| (retired_index($rows)) as $retired
| ($rows | map(select((.id | s) != "") | {key: (.id | s), value: true}) | from_entries) as $all_ids
| ([$rows[] | select(is_decision and (($retired[(.id | s)] // false) | not))]) as $live_decisions
| ([$rows[] | select(is_carry and ((.id | s) != ""))]) as $carries
| ([$live_decisions[]
    | . as $decision
    | (if (.disposition | s) == "DEFER" then "D"
       elif .enforcement_invariant == true then "I" else "S" end) as $tag
    | ({tag: $tag, line: decision_row},
       ($carries[] | select((.ref | s) == ($decision.id | s)) | {tag: $tag, line: carry_row}))
  ]) as $generated
| ($body | split("\n")) as $lines
| (reduce range(0; $lines | length) as $i ({fence: null, starts: [], ends: []};
    ($lines[$i] | rtrimstr("\r")) as $line
    | .fence as $open_fence
    | if $open_fence == null then
        ($line | fence_open) as $opened
        | if $opened != null then .fence = $opened
          elif $line == $block_start then .starts += [$i]
          elif $line == $block_end then .ends += [$i]
          else . end
      elif ($line | fence_close($open_fence)) then .fence = null
      else . end)) as $scan
| (if $digest then "none"
   elif ($scan.starts | length) == 0 and ($scan.ends | length) == 0 then "none"
   elif ($scan.starts | length) == 1 and ($scan.ends | length) == 1 and $scan.starts[0] < $scan.ends[0] then "one"
   else ("render: the PR body holds an unpaired or repeated code-review:deferred delimiter outside a code fence; fix the PR body by hand\n" | halt_error(3))
   end) as $blocks
| (if $blocks == "one" then $lines[$scan.starts[0] + 1 : $scan.ends[0]] else [] end) as $old_block
# A line in the old block that is not a recognized row shape is dropped, so a hand-edited block loses it.
| (reduce range(0; $old_block | length) as $i ({section: "D", kept: []};
    $old_block[$i] as $line
    | if ($line | test("^##\\s")) then .section = (if ($line | test("Settled")) then "S" else "D" end)
      elif ($line | test("^###\\s")) then .section = (if .section == "D" then "D" else "I" end)
      elif ($line | is_row_line) and (($line | is_separator_line) | not)
           and ((($i + 1 < ($old_block | length)) and ($old_block[$i + 1] | is_separator_line)) | not) then
        (if ($all_ids[($line | last_cell)] // false) then .
         else .kept += [{tag: .section, line: ($line | rtrimstr("\r") | gsub("<!--"; "&lt;!--"))}] end)
      else . end)
  | .kept) as $kept
| ("*Decided* is the UTC date and review round. *Id* is the ledger row id. A row marked *orchestrator-matched carry of* an id is a later finding the orchestrator matched to the decision above it, which the engineer did not see.") as $legend
| ("| Finding | Source | DEFER criterion | Rationale | Decided | Id |") as $deferred_header
| ("| Finding | Source | Decided by | Rationale | Decided | Id |") as $settled_header
| ("| --- | --- | --- | --- | --- | --- |") as $separator
| ([($generated[], $kept[]) | select(.tag == "D") | .line]) as $deferred_rows
| ([($generated[], $kept[]) | select(.tag == "S") | .line]) as $settled_rows
| ([($generated[], $kept[]) | select(.tag == "I") | .line]) as $invariant_rows
| (if ($deferred_rows | length) == 0 then []
   else ["## Deferred review findings", "", $legend, "", $deferred_header, $separator] + $deferred_rows end) as $deferred_section
| (if ($settled_rows | length) == 0 and ($invariant_rows | length) == 0 then []
   else ["## Settled review findings", "", $legend, ""]
        + (if ($settled_rows | length) == 0 then []
           else [$settled_header, $separator] + $settled_rows end)
        + (if ($invariant_rows | length) == 0 then []
           else (if ($settled_rows | length) == 0 then [] else [""] end)
                + ["### Enforcement-invariant decisions (asked again on every repeat)", "", $settled_header, $separator]
                + $invariant_rows end)
   end) as $settled_section
| ([$deferred_section, $settled_section] | map(select(length > 0))) as $sections
| (if ($sections | length) == 0 then []
   else [$block_start] + $sections[0] + ([$sections[1:][] | [""] + .] | add // []) + ["", $block_end] end) as $block_lines
| if $digest then
    (if ($block_lines | length) == 0 then "" else ($block_lines | join("\n")) + "\n" end)
  elif $blocks == "one" then
    ($lines[0 : $scan.starts[0]] + $block_lines + $lines[$scan.ends[0] + 1 :]) | join("\n")
  elif ($block_lines | length) == 0 then $body
  else
    $body
    + (if $body == "" or ($body | endswith("\n\n")) then ""
       elif ($body | endswith("\n")) then "\n" else "\n\n" end)
    + ($block_lines | join("\n")) + "\n"
  end
'

# _review_ledger_check_out REPO_ROOT OUT PR_JSON LEDGER_DIR
# Accepts OUT only as a regular-file target directly under REPO_ROOT's real
# agent-reviews directory, named review-ledger-<suffix>.md or pr-body-<suffix>.md.
# A relative OUT is taken from the working directory,
# as the write below takes it. Rejects a '..', '.' or empty segment, a symlinked
# agent-reviews directory or target, an existing target that is not a regular
# file, the same file as PR_JSON, and any directory at or under LEDGER_DIR.
# Prints nothing. Returns 1 with the reason on stderr.
_review_ledger_check_out() {
  local repo_root="$1" out="$2" pr_json="$3" ledger_dir="$4"
  local real_root here abs relative_out rel name agents_dir target pr_json_abs ledger_real origin_note=""
  real_root=$(cd -P -- "$repo_root" 2>/dev/null && pwd -P) || {
    _review_ledger_reject "render: could not resolve the repository root $repo_root"
    return 1
  }
  agents_dir="$real_root/agent-reviews"
  here=$(pwd -P) || return 1
  case "$out" in
    /*) abs="$out" ;;
    *)
      relative_out="$out"
      while [ "${relative_out#./}" != "$relative_out" ]; do
        relative_out="${relative_out#./}"
      done
      abs="$here/$relative_out"
      origin_note=" (a relative path, resolved from the working directory $here)"
      ;;
  esac
  case "$abs" in
    "$agents_dir"/?*) ;;
    *)
      _review_ledger_reject "render: --out '$out'$origin_note must be a file directly under $agents_dir/"
      return 1
      ;;
  esac
  rel=$(_review_ledger_repo_relative_path "$real_root" "$out" "$abs") || return 1
  name="${rel#agent-reviews/}"
  case "$name" in
    */*)
      _review_ledger_reject "render: --out '$out'$origin_note must be a file directly under $agents_dir/, not in a subdirectory"
      return 1
      ;;
  esac
  case "$name" in
    review-ledger-?*.md|pr-body-?*.md) ;;
    *)
      _review_ledger_reject "render: --out '$out' must be named review-ledger-<suffix>.md or pr-body-<suffix>.md, since --out deletes its target and no other file under agent-reviews/ is a render output"
      return 1
      ;;
  esac
  if [ -e "$agents_dir" ] || [ -L "$agents_dir" ]; then
    if [ -L "$agents_dir" ] || [ ! -d "$agents_dir" ]; then
      _review_ledger_reject "render: $agents_dir must be a real directory, not a symlink or a file"
      return 1
    fi
  fi
  target="$agents_dir/$name"
  if [ -L "$target" ]; then
    _review_ledger_reject "render: --out '$out' is a symlink; name a regular file"
    return 1
  fi
  if [ -e "$target" ] && [ ! -f "$target" ]; then
    _review_ledger_reject "render: --out '$out' exists and is not a regular file"
    return 1
  fi
  if [ -n "$pr_json" ] && [ "$pr_json" != "-" ]; then
    case "$pr_json" in
      /*) pr_json_abs="$pr_json" ;;
      *) pr_json_abs="$here/$pr_json" ;;
    esac
    if [ "$pr_json_abs" = "$target" ] || [ "$pr_json_abs" -ef "$target" ]; then
      _review_ledger_reject "render: --out and --pr-json name the same file, which --out deletes before it is read"
      return 1
    fi
  fi
  # Confinement to a direct child of agent-reviews/ is the control; this check only fires when the repository sits at or under the ledger directory.
  [ -n "$ledger_dir" ] || return 0
  ledger_real=$(cd -P -- "$ledger_dir" 2>/dev/null && pwd -P) || ledger_real="$ledger_dir"
  case "$agents_dir/" in
    "$ledger_real"/*|"$ledger_dir"/*)
      _review_ledger_reject "render: --out '$out' is inside the ledger directory $ledger_dir"
      return 1
      ;;
  esac
}

# _review_ledger_remove_out OUT
# Deletes a stale OUT so that no earlier render's body survives a later failure.
# An absent OUT is fine. Returns 1 with the reason on stderr when OUT is a
# symlink or not a regular file, or when the delete fails.
_review_ledger_remove_out() {
  local out="$1"
  [ -e "$out" ] || [ -L "$out" ] || return 0
  if [ -L "$out" ] || [ ! -f "$out" ]; then
    _review_ledger_reject "render: $out exists and is not a regular file, so it is left in place"
    return 1
  fi
  rm -f -- "$out"
  if [ -e "$out" ]; then
    _review_ledger_reject "render: could not delete the stale $out"
    return 1
  fi
}

# _review_ledger_render LEDGER_FILE PR_JSON OUT
# Renders the PR-body block from LEDGER_FILE's live decisions, and only that file.
# The block has no size cap. It grows with every live decision and every carry
# of one, so a body past the host's length limit makes the later `gh pr edit`
# fail.
# PR_JSON empty: the block alone (the reviewer digest). PR_JSON a path or "-":
# the JSON of `gh pr view --json body`, whose body is returned with the block
# replaced, appended when absent, and dropped when it would hold no rows. Every
# byte outside the block stays identical.
# OUT empty: the result goes to stdout. OUT set: OUT is deleted first, then
# written atomically only on success, and a `changed: <path>` or `unchanged`
# line goes to stdout. A digest with no live decision writes no file.
# Returns 1 with no OUT file on zero bytes of input, a failed ledger read, an
# unpaired delimiter, two blocks, or an OUT that cannot be deleted. Where OUT
# may point is _review_ledger_check_out's rule, not this function's.
_review_ledger_render() {
  local ledger_file="$1" pr_json="$2" out="$3"
  local work status=0
  if [ -n "$out" ]; then
    _review_ledger_remove_out "$out" || return 1
    mkdir -p -- "$(dirname -- "$out")" || {
      _review_ledger_reject "render: could not create the directory for $out"
      return 1
    }
  fi
  work=$(mktemp -d "${TMPDIR:-/tmp}/review-ledger.XXXXXX") || {
    _review_ledger_reject "render: could not create a temporary directory"
    return 1
  }
  _review_ledger_render_in "$ledger_file" "$pr_json" "$out" "$work" || status=$?
  rm -f -- "$work/body" "$work/result"
  rmdir -- "$work" 2>/dev/null
  return "$status"
}

# _review_ledger_render_in LEDGER_FILE PR_JSON OUT WORK_DIR
# The body of _review_ledger_render, with WORK_DIR for its intermediate files.
_review_ledger_render_in() {
  local ledger_file="$1" pr_json="$2" out="$3" work="$4"
  local rows input digest_flag=true out_tmp
  _review_ledger_check_regular_file "$ledger_file" || return 1
  rows=$(_review_ledger_read_rows "$ledger_file") || {
    _review_ledger_reject "render: could not read the ledger (jq missing, failed, or timed out)"
    return 1
  }
  : > "$work/body"
  if [ -n "$pr_json" ]; then
    digest_flag=false
    if [ "$pr_json" = "-" ]; then
      input=$(cat)
    else
      input=$(cat -- "$pr_json") || {
        _review_ledger_reject "render: could not read --pr-json file '$pr_json'"
        return 1
      }
    fi
    if [ -z "$input" ]; then
      _review_ledger_reject "render: --pr-json input is empty (a failed 'gh pr view'?). No file written."
      return 1
    fi
    printf '%s' "$input" | _lib_jq -n -j 'input
      | if type == "object" and (.body | type) == "string" then .body
        elif type == "object" and .body == null then ""
        else error("not a PR body object") end' > "$work/body" || {
      _review_ledger_reject "render: --pr-json input is not the JSON of 'gh pr view --json body'. No file written."
      return 1
    }
  fi
  printf '%s\n' "$rows" | _lib_jq -n -j --rawfile body "$work/body" --argjson digest "$digest_flag" \
    --arg block_start "$_REVIEW_LEDGER_DELIM_START" --arg block_end "$_REVIEW_LEDGER_DELIM_END" \
    "$_REVIEW_LEDGER_JQ_COMMON$_REVIEW_LEDGER_JQ_RENDER" > "$work/result" || {
    _review_ledger_reject "render failed (see above). No file written."
    return 1
  }
  if [ -z "$out" ]; then
    cat -- "$work/result"
    return 0
  fi
  if [ -n "$pr_json" ]; then
    if cmp -s -- "$work/body" "$work/result"; then
      printf 'unchanged\n'
      return 0
    fi
  elif [ ! -s "$work/result" ]; then
    printf 'unchanged\n'
    return 0
  fi
  out_tmp=$(mktemp "$out.XXXXXX") || {
    _review_ledger_reject "render: could not create a temporary file beside $out"
    return 1
  }
  if cat -- "$work/result" > "$out_tmp" && mv -f -- "$out_tmp" "$out"; then
    printf 'changed: %s\n' "$out"
    return 0
  fi
  rm -f -- "$out_tmp" "$out"
  _review_ledger_reject "render: could not write $out"
  return 1
}
