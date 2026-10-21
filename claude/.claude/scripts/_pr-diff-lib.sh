#!/usr/bin/env bash
# _pr-diff-lib.sh -- parsing, formatting and checking helpers for pr-diff-against-base.sh's --context-files mode.
#
# Sourced lazily by pr-diff-against-base.sh inside that mode only, so the legacy modes never depend on it.
# Not executable on its own; source it, do not invoke it directly.
# A function returns its result in PR_DIFF_RESULT or in a documented PR_DIFF_* array, never on stdout, so a caller inside a per-path loop forks nothing.
# test_pr_diff_lib.py sources this file directly; docs/scripts.md describes the mode itself.
#
# shellcheck disable=SC2034 # every PR_DIFF_* and *_SECONDS/*_BYTES* variable here is an output or a constant read by the sourcing script.

# Local budgets with no external spec; the per-call caps bound each git and gh call, and the local work between calls is uncapped.
CONTEXT_GIT_CALL_SECONDS=20
CONTEXT_DIFF_CALL_SECONDS=60
CONTEXT_ENRICHMENT_SECONDS=120
HISTORY_COMMITS_PER_PATH=5

# A cap, not a target: past it a body is omitted and its index row says so. These are local budgets with no external spec.
BASE_BYTES_PER_PATH=262144
HISTORY_BYTES_PER_PATH=131072
CONTEXT_BYTES_PER_LANE=1048576

# Closed list of the step names the script prints in a halt line; docs/scripts.md carries one remedy entry per name.
PR_DIFF_STEP_NAMES=(
  repo-root rev-parse base-resolution merge-base core-raw core-numstat untracked-list
  expect-diff status-check listing-id ignore-check output-dir temp-dir
  lane-diff header-check lane-slice enrichment write
)

PR_DIFF_HALT_POINTER='see docs/scripts.md § "When --context-files halts"'

# Canonical order of the notes column in a lane index row.
PR_DIFF_NOTE_ORDER=(
  binary gitlink symlink mode empty no-eol withheld over-cap commits-only unavailable skipped untracked
)

# _pr_diff_halt_line STEP STATUS [DETAIL]
# Prints the one stderr line every exit-1 halt ends with; an empty STEP leaves the "at <step>" clause out.
_pr_diff_halt_line() {
  local step=$1 status=$2 detail=${3-} line='pr-diff-against-base.sh: --context-files failed'
  if [ -n "$step" ]; then
    line+=" at $step"
  fi
  line+=" (exit $status)"
  if [ -n "$detail" ]; then
    line+=": $detail"
  fi
  printf '%s; %s\n' "$line" "$PR_DIFF_HALT_POINTER"
}

# ---------------------------------------------------------------- argument grammar

_pr_diff_valid_suffix() {
  local LC_ALL=C
  [[ $1 =~ ^[0-9]{1,20}-[A-Za-z0-9-]{0,20}$ ]]
}

_pr_diff_valid_agent_name() {
  local LC_ALL=C
  [[ $1 =~ ^[a-z][a-z0-9-]{0,63}$ ]]
}

_pr_diff_valid_listing_id() {
  local LC_ALL=C
  [[ $1 =~ ^[0-9a-f]{64}$ ]]
}

# No leading zero and no sign, so a number has one spelling.
_pr_diff_valid_ordinal() {
  local LC_ALL=C
  [[ $1 =~ ^[1-9][0-9]{0,5}$ ]]
}

# _pr_diff_parse_lane ARG
# Sets PR_DIFF_LANE_AGENT, PR_DIFF_LANE_FORM (whole, diff or listed) and PR_DIFF_LANE_NUMBERS (space-separated, duplicates collapsed, in first-seen order).
# Returns 1 with PR_DIFF_USAGE_ERROR set when ARG is malformed.
_pr_diff_parse_lane() {
  local arg=$1 agent form=whole list='' rest number numbers=''
  PR_DIFF_LANE_AGENT='' PR_DIFF_LANE_FORM='' PR_DIFF_LANE_NUMBERS='' PR_DIFF_USAGE_ERROR=''
  case $arg in
    *=*)
      agent=${arg%%=*}
      list=${arg#*=}
      form=listed
      ;;
    *:diff)
      agent=${arg%:diff}
      form='diff'
      ;;
    *) agent=$arg ;;
  esac
  if ! _pr_diff_valid_agent_name "$agent"; then
    _pr_diff_index_path "$arg"
    PR_DIFF_USAGE_ERROR="lane $PR_DIFF_RESULT does not start with a valid agent name"
    return 1
  fi
  if [ "$form" = listed ]; then
    case $list in
      '' | ,* | *, | *,,*)
        _pr_diff_index_path "$arg"
        PR_DIFF_USAGE_ERROR="lane $PR_DIFF_RESULT has an empty number in its list"
        return 1
        ;;
    esac
    rest=$list
    while :; do
      number=${rest%%,*}
      if ! _pr_diff_valid_ordinal "$number"; then
        _pr_diff_index_path "$number"
        PR_DIFF_USAGE_ERROR="lane number $PR_DIFF_RESULT is not a positive integer without a leading zero"
        return 1
      fi
      case " $numbers " in
        *" $number "*) ;;
        *) numbers+="${numbers:+ }$number" ;;
      esac
      case $rest in
        *,*) rest=${rest#*,} ;;
        *) break ;;
      esac
    done
  fi
  PR_DIFF_LANE_AGENT=$agent
  PR_DIFF_LANE_FORM=$form
  PR_DIFF_LANE_NUMBERS=$numbers
}

# _pr_diff_lane_row_indices FORM NUMBERS ROW_COUNT
# Fills PR_DIFF_LANE_ROWS with the 0-based row indices a lane reviews, ascending.
# A whole-diff or diff-only lane reviews every row; a listed lane reviews the rows its 1-based NUMBERS name.
_pr_diff_lane_row_indices() {
  local form=$1 numbers=$2 row_count=$3 index number
  local -a member=()
  PR_DIFF_LANE_ROWS=()
  if [ "$form" = listed ]; then
    for number in $numbers; do
      member[number - 1]=1
    done
  fi
  for ((index = 0; index < row_count; index++)); do
    if [ "$form" != listed ] || [ -n "${member[index]-}" ]; then
      PR_DIFF_LANE_ROWS+=("$index")
    fi
  done
}

# ---------------------------------------------------------------- path quoting

# Git quotes a name in a diff header when it holds a control byte, a double quote or a backslash; bytes of 0x80 and above stay raw under core.quotePath=false.
# _pr_diff_cquote QUOTES any text; the two predicates below decide when a caller does.
_pr_diff_cquote() {
  local LC_ALL=C text=$1 quoted='' char index code backslash=$'\\'
  for ((index = 0; index < ${#text}; index++)); do
    char=${text:index:1}
    case $char in
      '"') quoted+='\"' ;;
      "$backslash") quoted+=$'\\\\' ;;
      $'\a') quoted+='\a' ;;
      $'\b') quoted+='\b' ;;
      $'\t') quoted+='\t' ;;
      $'\n') quoted+='\n' ;;
      $'\v') quoted+='\v' ;;
      $'\f') quoted+='\f' ;;
      $'\r') quoted+='\r' ;;
      [[:cntrl:]])
        printf -v code '\\%03o' "'$char"
        quoted+=$code
        ;;
      *) quoted+=$char ;;
    esac
  done
  PR_DIFF_RESULT="\"$quoted\""
}

# An index, listing or token path is quoted when it holds a control byte or begins with a double quote, so a leading quote always means "quoted".
_pr_diff_index_path() {
  local LC_ALL=C
  case $1 in
    \"* | *[[:cntrl:]]*) _pr_diff_cquote "$1" ;;
    *) PR_DIFF_RESULT=$1 ;;
  esac
}

# A diff header name is quoted when it holds a control byte, a double quote or a backslash.
_pr_diff_git_quote_name() {
  local LC_ALL=C
  case $1 in
    *[[:cntrl:]\"\\]*) _pr_diff_cquote "$1" ;;
    *) PR_DIFF_RESULT=$1 ;;
  esac
}

# _pr_diff_cunquote TEXT
# Decodes a git C-quoted name (\\ \" \a \b \f \n \r \t \v \ooo); text that is not wrapped in double quotes comes back unchanged.
_pr_diff_cunquote() {
  local LC_ALL=C text=$1 inner decoded='' index char octal backslash=$'\\'
  case $text in
    \"*\") ;;
    *)
      PR_DIFF_RESULT=$text
      return 0
      ;;
  esac
  inner=${text:1:${#text}-2}
  for ((index = 0; index < ${#inner}; index++)); do
    char=${inner:index:1}
    if [ "$char" != "$backslash" ]; then
      decoded+=$char
      continue
    fi
    index=$((index + 1))
    char=${inner:index:1}
    case $char in
      a) decoded+=$'\a' ;;
      b) decoded+=$'\b' ;;
      f) decoded+=$'\f' ;;
      n) decoded+=$'\n' ;;
      r) decoded+=$'\r' ;;
      t) decoded+=$'\t' ;;
      v) decoded+=$'\v' ;;
      [0-7])
        octal=$char
        while [ "${#octal}" -lt 3 ] && [[ ${inner:index+1:1} == [0-7] ]]; do
          index=$((index + 1))
          octal+=${inner:index:1}
        done
        printf -v char '%b' "\\0$octal"
        decoded+=$char
        ;;
      *) decoded+=$char ;;
    esac
  done
  PR_DIFF_RESULT=$decoded
}

# _pr_diff_split_header_tokens TEXT
# Splits TEXT on spaces outside double quotes into PR_DIFF_TOKENS, keeping each quoted token's quotes.
_pr_diff_split_header_tokens() {
  local LC_ALL=C text=$1 index char token='' in_quote=0 backslash=$'\\'
  PR_DIFF_TOKENS=()
  for ((index = 0; index < ${#text}; index++)); do
    char=${text:index:1}
    if [ "$in_quote" -eq 1 ]; then
      token+=$char
      if [ "$char" = "$backslash" ]; then
        index=$((index + 1))
        token+=${text:index:1}
      elif [ "$char" = '"' ]; then
        in_quote=0
      fi
    elif [ "$char" = '"' ]; then
      token+=$char
      in_quote=1
    elif [ "$char" = ' ' ]; then
      if [ -n "$token" ]; then
        PR_DIFF_TOKENS+=("$token")
        token=''
      fi
    else
      token+=$char
    fi
  done
  if [ -n "$token" ]; then
    PR_DIFF_TOKENS+=("$token")
  fi
}

# _pr_diff_history_header_names LINE
# Fills PR_DIFF_HEADER_NAMES with the path names a git patch header line carries, C-unquoted and without the a/ or b/ prefix.
# A line that is not a name-bearing header leaves it empty.
_pr_diff_history_header_names() {
  local line=$1 name token
  PR_DIFF_HEADER_NAMES=()
  case $line in
    'diff --git '*)
      _pr_diff_split_header_tokens "${line#diff --git }"
      for token in "${PR_DIFF_TOKENS[@]}"; do
        _pr_diff_cunquote "$token"
        name=$PR_DIFF_RESULT
        name=${name#[ab]/}
        PR_DIFF_HEADER_NAMES+=("$name")
      done
      ;;
    '--- '* | '+++ '*)
      name=${line:4}
      name=${name%$'\t'}
      if [ "$name" != /dev/null ]; then
        _pr_diff_cunquote "$name"
        name=$PR_DIFF_RESULT
        PR_DIFF_HEADER_NAMES+=("${name#[ab]/}")
      fi
      ;;
    'rename from '*)
      _pr_diff_cunquote "${line#rename from }"
      PR_DIFF_HEADER_NAMES+=("$PR_DIFF_RESULT")
      ;;
    'rename to '*)
      _pr_diff_cunquote "${line#rename to }"
      PR_DIFF_HEADER_NAMES+=("$PR_DIFF_RESULT")
      ;;
  esac
}

# ---------------------------------------------------------------- notes, ranges, lane membership

# _pr_diff_canonical_notes NOTE...
# Joins the given note names in the index's canonical order, comma-separated, or "-" when none is present.
_pr_diff_canonical_notes() {
  local given=" $* " note joined=''
  for note in "${PR_DIFF_NOTE_ORDER[@]}"; do
    case $given in
      *" $note "*) joined+="${joined:+,}$note" ;;
    esac
  done
  PR_DIFF_RESULT=${joined:--}
}

# _pr_diff_next_range CURSOR_VARIABLE LINE_COUNT
# Returns the "<start>+<count>" range of the next body of LINE_COUNT lines and advances the named cursor, which holds the next 1-based start line; "-" for an empty body.
_pr_diff_next_range() {
  local -n range_cursor=$1
  local line_count=$2
  if [ "$line_count" -le 0 ]; then
    PR_DIFF_RESULT=-
    return 0
  fi
  PR_DIFF_RESULT="$range_cursor+$line_count"
  range_cursor=$((range_cursor + line_count))
}

# _pr_diff_budget_exhausted START_SECONDS NOW_SECONDS LIMIT_SECONDS
# True once the enrichment budget, measured from the first enrichment call, is used up.
_pr_diff_budget_exhausted() {
  [ $(($2 - $1)) -ge "$3" ]
}

# _pr_diff_enrichment_has_time
# True while the enrichment budget, measured from the first call, has time left; the first call starts the clock in ENRICH_START and the start is never reset.
_pr_diff_enrichment_has_time() {
  if [ -z "$ENRICH_START" ]; then
    ENRICH_START=$SECONDS
    return 0
  fi
  ! _pr_diff_budget_exhausted "$ENRICH_START" "$SECONDS" "$CONTEXT_ENRICHMENT_SECONDS"
}

# _pr_diff_enrichment_order
# Fills PR_DIFF_ENRICH_ORDER (0-based row indices) from PR_DIFF_LANE_FORM_LIST and PR_DIFF_LANE_NUMBERS_LIST: rows a listed lane names first in index order, then rows only a whole-diff lane reaches.
# A diff-only lane enriches nothing.
_pr_diff_enrichment_order() {
  local lane index number whole_lane_present=0 row_count=${#PR_DIFF_ROW_STATUS[@]}
  local -a listed_flag=()
  PR_DIFF_ENRICH_ORDER=()
  for ((lane = 0; lane < ${#PR_DIFF_LANE_FORM_LIST[@]}; lane++)); do
    case ${PR_DIFF_LANE_FORM_LIST[lane]} in
      listed)
        for number in ${PR_DIFF_LANE_NUMBERS_LIST[lane]}; do
          listed_flag[number - 1]=1
        done
        ;;
      whole) whole_lane_present=1 ;;
    esac
  done
  for ((index = 0; index < row_count; index++)); do
    if [ -n "${listed_flag[index]-}" ]; then
      PR_DIFF_ENRICH_ORDER+=("$index")
    fi
  done
  if [ "$whole_lane_present" -eq 1 ]; then
    for ((index = 0; index < row_count; index++)); do
      if [ -z "${listed_flag[index]-}" ]; then
        PR_DIFF_ENRICH_ORDER+=("$index")
      fi
    done
  fi
}

# Compares two texts after stripping every trailing newline from each, the way command substitution does.
_pr_diff_normalized_equal() {
  local left=$1 right=$2
  while [[ $left == *$'\n' ]]; do
    left=${left%$'\n'}
  done
  while [[ $right == *$'\n' ]]; do
    right=${right%$'\n'}
  done
  [[ $left == "$right" ]]
}

# _pr_diff_count_lines FILE MAX_BYTES
# Sets PR_DIFF_LINES, PR_DIFF_BYTES (as copied, counting the newline a missing final one would get), PR_DIFF_NOEOL (1 when the last line has no newline) and PR_DIFF_EXCEEDS (1, with reading stopped, once the bytes pass MAX_BYTES).
# Reads in the shell, so the caller forks nothing per file.
_pr_diff_count_lines() {
  local LC_ALL=C file=$1 max_bytes=$2 line=''
  PR_DIFF_LINES=0 PR_DIFF_BYTES=0 PR_DIFF_NOEOL=0 PR_DIFF_EXCEEDS=0
  while IFS= read -r line; do
    PR_DIFF_LINES=$((PR_DIFF_LINES + 1))
    PR_DIFF_BYTES=$((PR_DIFF_BYTES + ${#line} + 1))
    if [ "$PR_DIFF_BYTES" -gt "$max_bytes" ]; then
      PR_DIFF_EXCEEDS=1
      return 0
    fi
  done < "$file"
  if [ -n "$line" ]; then
    PR_DIFF_LINES=$((PR_DIFF_LINES + 1))
    PR_DIFF_BYTES=$((PR_DIFF_BYTES + ${#line} + 1))
    PR_DIFF_NOEOL=1
    if [ "$PR_DIFF_BYTES" -gt "$max_bytes" ]; then
      PR_DIFF_EXCEEDS=1
    fi
  fi
}

# ---------------------------------------------------------------- raw and numstat records

# _pr_diff_parse_raw FILE
# Fills the PR_DIFF_ROW_* arrays from `git diff --raw -z --no-abbrev` output.
# PR_DIFF_ROW_PATH holds the current name and PR_DIFF_ROW_OLD_PATH the pre-image name (equal unless the row is a rename or copy).
# Returns 1 with PR_DIFF_PARSE_ERROR set on a record that does not parse.
_pr_diff_parse_raw() {
  local file=$1 position=0 meta rest status
  local -a tokens=()
  PR_DIFF_ROW_STATUS=() PR_DIFF_ROW_OLD_MODE=() PR_DIFF_ROW_NEW_MODE=() PR_DIFF_ROW_OLD_BLOB=()
  PR_DIFF_ROW_NEW_BLOB=() PR_DIFF_ROW_PATH=() PR_DIFF_ROW_OLD_PATH=() PR_DIFF_PARSE_ERROR=''
  mapfile -d '' -t tokens < "$file" || {
    PR_DIFF_PARSE_ERROR='could not read the raw output'
    return 1
  }
  while [ "$position" -lt "${#tokens[@]}" ]; do
    meta=${tokens[position]}
    position=$((position + 1))
    case $meta in
      :*) ;;
      *)
        PR_DIFF_PARSE_ERROR='a raw record does not start with a mode line'
        return 1
        ;;
    esac
    meta=${meta#:}
    PR_DIFF_ROW_OLD_MODE+=("${meta%% *}")
    rest=${meta#* }
    PR_DIFF_ROW_NEW_MODE+=("${rest%% *}")
    rest=${rest#* }
    PR_DIFF_ROW_OLD_BLOB+=("${rest%% *}")
    rest=${rest#* }
    PR_DIFF_ROW_NEW_BLOB+=("${rest%% *}")
    status=${rest#* }
    PR_DIFF_ROW_STATUS+=("$status")
    case $status in
      R* | C*)
        if [ $((position + 1)) -ge "${#tokens[@]}" ]; then
          PR_DIFF_PARSE_ERROR='a rename or copy record is missing a path'
          return 1
        fi
        PR_DIFF_ROW_OLD_PATH+=("${tokens[position]}")
        PR_DIFF_ROW_PATH+=("${tokens[position + 1]}")
        position=$((position + 2))
        ;;
      *)
        if [ "$position" -ge "${#tokens[@]}" ]; then
          PR_DIFF_PARSE_ERROR='a record is missing its path'
          return 1
        fi
        PR_DIFF_ROW_OLD_PATH+=("${tokens[position]}")
        PR_DIFF_ROW_PATH+=("${tokens[position]}")
        position=$((position + 1))
        ;;
    esac
  done
  return 0
}

# _pr_diff_append_untracked FILE
# Appends one "?" row per NUL-separated path in FILE, with no modes or blobs.
_pr_diff_append_untracked() {
  local path
  local -a paths=()
  mapfile -d '' -t paths < "$1"
  for path in "${paths[@]}"; do
    PR_DIFF_ROW_STATUS+=('?')
    PR_DIFF_ROW_OLD_MODE+=('')
    PR_DIFF_ROW_NEW_MODE+=('')
    PR_DIFF_ROW_OLD_BLOB+=('')
    PR_DIFF_ROW_NEW_BLOB+=('')
    PR_DIFF_ROW_PATH+=("$path")
    PR_DIFF_ROW_OLD_PATH+=("$path")
  done
}

# _pr_diff_check_status_grammar
# Returns 1, with PR_DIFF_BAD_ROW set to its 0-based index, on the first row whose status is outside A, M, D, T, R<nnn>, C<nnn> and "?".
_pr_diff_check_status_grammar() {
  local index
  for ((index = 0; index < ${#PR_DIFF_ROW_STATUS[@]}; index++)); do
    case ${PR_DIFF_ROW_STATUS[index]} in
      A | M | D | T | '?') ;;
      R[0-9][0-9][0-9] | C[0-9][0-9][0-9]) ;;
      *)
        PR_DIFF_BAD_ROW=$index
        return 1
        ;;
    esac
  done
}

# _pr_diff_parse_numstat FILE
# Fills PR_DIFF_NUMSTAT, keyed by the current name, with "<added> <deleted>" ("- -" for a binary file), and sets PR_DIFF_NUMSTAT_COUNT.
# Returns 1 with PR_DIFF_PARSE_ERROR set on a record that does not parse.
_pr_diff_parse_numstat() {
  local file=$1 position=0 record added rest deleted name
  local -a tokens=()
  declare -gA PR_DIFF_NUMSTAT=()
  PR_DIFF_NUMSTAT_COUNT=0 PR_DIFF_PARSE_ERROR=''
  mapfile -d '' -t tokens < "$file" || {
    PR_DIFF_PARSE_ERROR='could not read the numstat output'
    return 1
  }
  while [ "$position" -lt "${#tokens[@]}" ]; do
    record=${tokens[position]}
    position=$((position + 1))
    case $record in
      *$'\t'*$'\t'*) ;;
      *)
        PR_DIFF_PARSE_ERROR='a numstat record does not hold two tab-separated counts'
        return 1
        ;;
    esac
    added=${record%%$'\t'*}
    rest=${record#*$'\t'}
    deleted=${rest%%$'\t'*}
    name=${rest#*$'\t'}
    if [ -z "$name" ]; then
      if [ $((position + 1)) -ge "${#tokens[@]}" ]; then
        PR_DIFF_PARSE_ERROR='a rename or copy numstat record is missing a path'
        return 1
      fi
      name=${tokens[position + 1]}
      position=$((position + 2))
    fi
    PR_DIFF_NUMSTAT[$name]="$added $deleted"
    PR_DIFF_NUMSTAT_COUNT=$((PR_DIFF_NUMSTAT_COUNT + 1))
  done
  return 0
}

# _pr_diff_classify_rows
# Joins the raw rows to PR_DIFF_NUMSTAT and fills PR_DIFF_ROW_NOTES, the space-separated classification notes of each row (binary, gitlink, symlink, mode, empty, untracked).
# Returns 1 with PR_DIFF_PARSE_ERROR set when a tracked row has no numstat entry or the numstat holds an entry for no row.
_pr_diff_classify_rows() {
  local index tracked=0 status old_mode new_mode counts added deleted notes
  PR_DIFF_ROW_NOTES=() PR_DIFF_PARSE_ERROR=''
  for ((index = 0; index < ${#PR_DIFF_ROW_STATUS[@]}; index++)); do
    status=${PR_DIFF_ROW_STATUS[index]}
    if [ "$status" = '?' ]; then
      PR_DIFF_ROW_NOTES[index]=untracked
      continue
    fi
    tracked=$((tracked + 1))
    if [ -z "${PR_DIFF_NUMSTAT[${PR_DIFF_ROW_PATH[index]}]+set}" ]; then
      _pr_diff_index_path "${PR_DIFF_ROW_PATH[index]}"
      PR_DIFF_PARSE_ERROR="no numstat entry for $PR_DIFF_RESULT"
      return 1
    fi
    counts=${PR_DIFF_NUMSTAT[${PR_DIFF_ROW_PATH[index]}]}
    added=${counts%% *}
    deleted=${counts#* }
    old_mode=${PR_DIFF_ROW_OLD_MODE[index]}
    new_mode=${PR_DIFF_ROW_NEW_MODE[index]}
    notes=''
    if [ "$added" = - ]; then
      notes+=' binary'
    fi
    if [ "$old_mode" = 160000 ] || [ "$new_mode" = 160000 ]; then
      notes+=' gitlink'
    fi
    if [ "$old_mode" = 120000 ] || [ "$new_mode" = 120000 ]; then
      notes+=' symlink'
    fi
    case $status in
      M | R* | C*)
        if [ "$old_mode" != "$new_mode" ]; then
          notes+=' mode'
        fi
        ;;
      A | D)
        if [ "$added" = 0 ] && [ "$deleted" = 0 ]; then
          notes+=' empty'
        fi
        ;;
    esac
    PR_DIFF_ROW_NOTES[index]=${notes# }
  done
  if [ "$tracked" -ne "$PR_DIFF_NUMSTAT_COUNT" ]; then
    PR_DIFF_PARSE_ERROR="the raw output holds $tracked rows and the numstat output $PR_DIFF_NUMSTAT_COUNT"
    return 1
  fi
}

# ---------------------------------------------------------------- withholding predicates

# _pr_diff_guard_file_path [NAME]
# Resolves an opt-in guard file (credential-file-guard.md unless NAME is given) the way the Read hooks do: the config dir's copy wins, else the legacy $HOME/.claude one.
# An unset HOME with no resolvable config dir reads as no guard file.
_pr_diff_guard_file_path() {
  local guard_name=${1:-credential-file-guard.md} config_dir
  PR_DIFF_RESULT="${HOME-}/.claude/$guard_name"
  if config_dir=$(_lib_config_dir) && [ -f "$config_dir/$guard_name" ]; then
    PR_DIFF_RESULT="$config_dir/$guard_name"
  fi
}

# _pr_diff_read_guard_globs FILE [ARRAY_NAME]
# Fills ARRAY_NAME (PR_DIFF_GUARD_GLOBS unless given) with the guard file's glob lines; an absent or unreadable file leaves it empty, as in the hook.
_pr_diff_read_guard_globs() {
  local lineno line
  local -n guard_globs=${2:-PR_DIFF_GUARD_GLOBS}
  guard_globs=()
  while IFS=$'\t' read -r lineno line; do
    guard_globs+=("$line")
  done < <(_lib_config_lines "$1")
}

# _pr_diff_load_data_guard
# Sets PR_DIFF_DATA_GUARD_ACTIVE to 1, and fills PR_DIFF_DATA_GUARD_GLOBS, only when data-file-read-guard.md resolves to a readable regular file, as deny-data-file-reads.sh arms itself.
_pr_diff_load_data_guard() {
  PR_DIFF_DATA_GUARD_ACTIVE=0 PR_DIFF_DATA_GUARD_GLOBS=()
  _pr_diff_guard_file_path data-file-read-guard.md
  if [ -f "$PR_DIFF_RESULT" ] && [ -r "$PR_DIFF_RESULT" ]; then
    PR_DIFF_DATA_GUARD_ACTIVE=1
    _pr_diff_read_guard_globs "$PR_DIFF_RESULT" PR_DIFF_DATA_GUARD_GLOBS
  fi
}

# _pr_diff_guard_glob_matches CASE_MODE ABSOLUTE_PATH GLOB...
# True when ABSOLUTE_PATH matches any GLOB; CASE_MODE is ignore-case (deny-credential-file-reads.sh's opt-in glob check) or match-case (deny-data-file-reads.sh's).
# test_pr_diff_lib.py's parity tests take those hooks' Read verdicts as the oracle.
# Runs `case` in the shell, so it forks nothing per path, and restores the caller's nocasematch setting.
_pr_diff_guard_glob_matches() {
  local case_mode=$1 candidate=$2 glob matched=1 nocasematch_before=off
  shift 2
  if shopt -q nocasematch; then
    nocasematch_before=on
  fi
  if [ "$case_mode" = ignore-case ]; then
    shopt -s nocasematch
  else
    shopt -u nocasematch
  fi
  for glob in "$@"; do
    # shellcheck disable=SC2254 # the glob is an intentional user-authored pattern, matched as the hook matches it.
    case $candidate in
      $glob)
        matched=0
        break
        ;;
    esac
  done
  if [ "$nocasematch_before" = on ]; then
    shopt -s nocasematch
  else
    shopt -u nocasematch
  fi
  return "$matched"
}

# _pr_diff_basename TEXT
# Sets PR_DIFF_RESULT to what `basename -- TEXT` yields inside a command substitution, as the hooks take it: trailing slashes go first, then the leading directories, then trailing newlines.
_pr_diff_basename() {
  local text=$1
  while [[ $text == */ ]]; do
    text=${text%/}
  done
  text=${text##*/}
  while [[ $text == *$'\n' ]]; do
    text=${text%$'\n'}
  done
  PR_DIFF_RESULT=$text
}

# _pr_diff_mark_credential_regex_hits ROOT
# Sets PR_DIFF_SCAN_HIT for each PR_DIFF_SCAN_TEXT entry whose "<ROOT>/<entry>" has a line matching _LIB_CREDENTIAL_PATH_REGEX, case-insensitively.
# Matches each newline-separated line apart, as deny-credential-file-reads.sh's here-string grep does, and in the shell, so it needs no particular grep dialect and forks nothing.
_pr_diff_mark_credential_regex_hits() {
  local LC_ALL=C root=$1 index remaining line nocasematch_before=off
  if shopt -q nocasematch; then
    nocasematch_before=on
  fi
  shopt -s nocasematch
  for ((index = 0; index < ${#PR_DIFF_SCAN_TEXT[@]}; index++)); do
    remaining="$root/${PR_DIFF_SCAN_TEXT[index]}"
    while :; do
      line=${remaining%%$'\n'*}
      if [[ $line =~ $_LIB_CREDENTIAL_PATH_REGEX ]]; then
        PR_DIFF_SCAN_HIT[index]=1
        break
      fi
      case $remaining in
        *$'\n'*) remaining=${remaining#*$'\n'} ;;
        *) break ;;
      esac
    done
  done
  if [ "$nocasematch_before" = off ]; then
    shopt -u nocasematch
  fi
}

# _pr_diff_mark_data_guard_hits ROOT
# Sets PR_DIFF_SCAN_HIT for each PR_DIFF_SCAN_TEXT entry deny-data-file-reads.sh would deny by name when read as "<ROOT>/<entry>": a data-file extension, a Downloads/ directory, or a guard glob.
# The hook's size rule is not copied: it applies to the lane file a reviewer reads, not to the path on disk.
_pr_diff_mark_data_guard_hits() {
  local root=$1 index text absolute
  for ((index = 0; index < ${#PR_DIFF_SCAN_TEXT[@]}; index++)); do
    text=${PR_DIFF_SCAN_TEXT[index]}
    absolute="$root/$text"
    _pr_diff_basename "$text"
    case $PR_DIFF_RESULT in
      *.csv | *.tsv | *.parquet | *.avro | *.xlsx | *.ndjson | *.jsonl | *.dump | *.bak | *.sqlite | *.db | *.dta | *.sav | *.pkl)
        PR_DIFF_SCAN_HIT[index]=1
        continue
        ;;
    esac
    case $absolute in
      */Downloads/*)
        PR_DIFF_SCAN_HIT[index]=1
        continue
        ;;
    esac
    if [ "${#PR_DIFF_DATA_GUARD_GLOBS[@]}" -gt 0 ] && _pr_diff_guard_glob_matches match-case "$absolute" "${PR_DIFF_DATA_GUARD_GLOBS[@]}"; then
      PR_DIFF_SCAN_HIT[index]=1
    fi
  done
}

# _pr_diff_scan_withheld ROOT
# Marks, in PR_DIFF_SCAN_HIT (parallel to PR_DIFF_SCAN_TEXT), each relative path the Read-side gates would deny.
# The checks are the built-in credential regex, the unsafe-.ssh-directory predicate, the dotenv basename rule and the opt-in credential guard globs, plus the data-file guard's rules while PR_DIFF_DATA_GUARD_ACTIVE is 1.
# Every name predicate except the dotenv rule and the data-file extension rule, which the hooks apply to the basename, sees "<ROOT>/<path>", as the Read hooks see an absolute path.
# Each runs once over the whole list, except that the .ssh predicate re-runs once per entry whose "<ROOT>/<path>" contains .ssh after its whole-list pass hits.
# The .ssh predicate's extraction grep -oEi is the call that fails open, reading as no match as the hook's own predicates do.
# A failing per-candidate grep -qEi reads as an unsafe basename, so it withholds.
_pr_diff_scan_withheld() {
  local root=$1 count=${#PR_DIFF_SCAN_TEXT[@]} index text joined
  PR_DIFF_SCAN_HIT=()
  for ((index = 0; index < count; index++)); do
    PR_DIFF_SCAN_HIT[index]=0
  done
  if [ "$count" -eq 0 ]; then
    return 0
  fi
  _pr_diff_mark_credential_regex_hits "$root"
  joined=''
  for text in "${PR_DIFF_SCAN_TEXT[@]}"; do
    joined+="$root/$text"$'\n'
  done
  if _lib_has_unsafe_ssh_dir_reference "$joined"; then
    for ((index = 0; index < count; index++)); do
      text="$root/${PR_DIFF_SCAN_TEXT[index]}"
      case ${text,,} in
        *.ssh*)
          if _lib_has_unsafe_ssh_dir_reference "$text"; then
            PR_DIFF_SCAN_HIT[index]=1
          fi
          ;;
      esac
    done
  fi
  for ((index = 0; index < count; index++)); do
    _pr_diff_basename "${PR_DIFF_SCAN_TEXT[index]}"
    if _lib_dotenv_basename_is_secret "$PR_DIFF_RESULT"; then
      PR_DIFF_SCAN_HIT[index]=1
    fi
  done
  if [ "${#PR_DIFF_GUARD_GLOBS[@]}" -gt 0 ]; then
    for ((index = 0; index < count; index++)); do
      if _pr_diff_guard_glob_matches ignore-case "$root/${PR_DIFF_SCAN_TEXT[index]}" "${PR_DIFF_GUARD_GLOBS[@]}"; then
        PR_DIFF_SCAN_HIT[index]=1
      fi
    done
  fi
  if [ "${PR_DIFF_DATA_GUARD_ACTIVE-0}" -eq 1 ]; then
    _pr_diff_mark_data_guard_hits "$root"
  fi
}

# ---------------------------------------------------------------- shared-diff headers and slicing

# _pr_diff_expected_header OLD_NAME NEW_NAME
# Builds the diff --git line git prints for a row; git quotes the a/ or b/ prefix and the name together, once per side.
_pr_diff_expected_header() {
  local left
  _pr_diff_git_quote_name "a/$1"
  left=$PR_DIFF_RESULT
  _pr_diff_git_quote_name "b/$2"
  PR_DIFF_RESULT="diff --git $left $PR_DIFF_RESULT"
}

# _pr_diff_build_expected_headers
# Fills PR_DIFF_ROW_HEADER, PR_DIFF_ROW_COPIES (2 for a type change, 0 for an untracked row, else 1) and the PR_DIFF_EXPECTED_COUNT / PR_DIFF_EXPECTED_ROW tables.
# Sets PR_DIFF_AMBIGUOUS_HEADER to the first header two different rows would both print.
_pr_diff_build_expected_headers() {
  local row status header copies
  declare -gA PR_DIFF_EXPECTED_COUNT=() PR_DIFF_EXPECTED_ROW=()
  PR_DIFF_ROW_HEADER=() PR_DIFF_ROW_COPIES=() PR_DIFF_AMBIGUOUS_HEADER=''
  for ((row = 0; row < ${#PR_DIFF_ROW_STATUS[@]}; row++)); do
    status=${PR_DIFF_ROW_STATUS[row]}
    if [ "$status" = '?' ]; then
      PR_DIFF_ROW_HEADER[row]=''
      PR_DIFF_ROW_COPIES[row]=0
      continue
    fi
    _pr_diff_expected_header "${PR_DIFF_ROW_OLD_PATH[row]}" "${PR_DIFF_ROW_PATH[row]}"
    header=$PR_DIFF_RESULT
    copies=1
    if [ "$status" = T ]; then
      copies=2
    fi
    if [ -n "${PR_DIFF_EXPECTED_ROW[$header]+set}" ] && [ "${PR_DIFF_EXPECTED_ROW[$header]}" != "$row" ] && [ -z "$PR_DIFF_AMBIGUOUS_HEADER" ]; then
      PR_DIFF_AMBIGUOUS_HEADER=$header
    fi
    PR_DIFF_ROW_HEADER[row]=$header
    PR_DIFF_ROW_COPIES[row]=$copies
    PR_DIFF_EXPECTED_ROW[$header]=$row
    PR_DIFF_EXPECTED_COUNT[$header]=$((${PR_DIFF_EXPECTED_COUNT[$header]-0} + copies))
  done
}

# _pr_diff_check_headers FOUND_FILE SHARED_BYTES
# Compares the diff --git lines found in the shared diff ("<byte offset>:<line>" records from grep -b) with the expected multiset.
# On success fills PR_DIFF_HDR_OFFSET and PR_DIFF_HDR_TEXT (file order) and PR_DIFF_ROW_HDRS (the found-header indices each row owns, space-separated).
# On a mismatch returns 1 with PR_DIFF_HEADER_DETAIL set: the first unexpected header in file order, else the first missing header in row order, else the counts.
# Table values are read into plain variables before any arithmetic, because an arithmetic context evaluates a subscript.
_pr_diff_check_headers() {
  local found_file=$1 shared_bytes=$2 record offset text key row index first_index value
  local expected_total=0 found_total=0 first_unexpected='' first_missing=''
  local -A found_count=() found_first=()
  PR_DIFF_HDR_OFFSET=() PR_DIFF_HDR_TEXT=() PR_DIFF_ROW_HDRS=() PR_DIFF_HEADER_DETAIL=''
  for ((row = 0; row < ${#PR_DIFF_ROW_STATUS[@]}; row++)); do
    PR_DIFF_ROW_HDRS[row]=''
  done
  if [ -n "$PR_DIFF_AMBIGUOUS_HEADER" ]; then
    _pr_diff_index_path "$PR_DIFF_AMBIGUOUS_HEADER"
    PR_DIFF_HEADER_DETAIL="ambiguous header $PR_DIFF_RESULT"
    return 1
  fi
  for key in "${!PR_DIFF_EXPECTED_COUNT[@]}"; do
    value=${PR_DIFF_EXPECTED_COUNT[$key]}
    expected_total=$((expected_total + value))
  done
  while IFS= read -r record; do
    offset=${record%%:*}
    text=${record#*:}
    PR_DIFF_HDR_OFFSET+=("$offset")
    PR_DIFF_HDR_TEXT+=("$text")
    if [ -z "${found_first[$text]+set}" ]; then
      found_first[$text]=$found_total
    fi
    value=${found_count[$text]-0}
    found_count[$text]=$((value + 1))
    found_total=$((found_total + 1))
  done < "$found_file" || {
    PR_DIFF_HEADER_DETAIL='could not read the found headers'
    return 1
  }
  if [ "$expected_total" -eq 0 ]; then
    if [ "$found_total" -ne 0 ]; then
      PR_DIFF_HEADER_DETAIL="expected 0 headers, found $found_total"
      return 1
    fi
    if [ "$shared_bytes" -ne 0 ]; then
      PR_DIFF_HEADER_DETAIL="expected 0 headers, found none in $shared_bytes bytes of output"
      return 1
    fi
    return 0
  fi
  for ((index = 0; index < found_total; index++)); do
    if [ -z "${PR_DIFF_EXPECTED_COUNT[${PR_DIFF_HDR_TEXT[index]}]+set}" ]; then
      first_unexpected=${PR_DIFF_HDR_TEXT[index]}
      break
    fi
  done
  if [ -n "$first_unexpected" ]; then
    _pr_diff_index_path "$first_unexpected"
    PR_DIFF_HEADER_DETAIL="unexpected header $PR_DIFF_RESULT"
    return 1
  fi
  for ((row = 0; row < ${#PR_DIFF_ROW_STATUS[@]}; row++)); do
    if [ "${PR_DIFF_ROW_COPIES[row]}" -gt 0 ] && [ -z "${found_count[${PR_DIFF_ROW_HEADER[row]}]+set}" ]; then
      first_missing=${PR_DIFF_ROW_PATH[row]}
      break
    fi
  done
  if [ -n "$first_missing" ]; then
    _pr_diff_index_path "$first_missing"
    PR_DIFF_HEADER_DETAIL="missing header for $PR_DIFF_RESULT"
    return 1
  fi
  for key in "${!PR_DIFF_EXPECTED_COUNT[@]}"; do
    if [ "${found_count[$key]-0}" -ne "${PR_DIFF_EXPECTED_COUNT[$key]}" ]; then
      PR_DIFF_HEADER_DETAIL="expected $expected_total headers, found $found_total"
      return 1
    fi
  done
  if [ "${PR_DIFF_HDR_OFFSET[0]}" -ne 0 ]; then
    PR_DIFF_HEADER_DETAIL="expected the first of $found_total headers at offset 0, found it at ${PR_DIFF_HDR_OFFSET[0]}"
    return 1
  fi
  for ((row = 0; row < ${#PR_DIFF_ROW_STATUS[@]}; row++)); do
    if [ "${PR_DIFF_ROW_COPIES[row]}" -eq 0 ]; then
      continue
    fi
    first_index=${found_first[${PR_DIFF_ROW_HEADER[row]}]}
    PR_DIFF_ROW_HDRS[row]=$first_index
    if [ "${PR_DIFF_ROW_COPIES[row]}" -eq 2 ]; then
      if [ "${PR_DIFF_HDR_TEXT[first_index + 1]-}" != "${PR_DIFF_ROW_HEADER[row]}" ]; then
        _pr_diff_index_path "${PR_DIFF_ROW_PATH[row]}"
        PR_DIFF_HEADER_DETAIL="the two headers for $PR_DIFF_RESULT are not adjacent"
        return 1
      fi
      PR_DIFF_ROW_HDRS[row]="$first_index $((first_index + 1))"
    fi
  done
}

# _pr_diff_block_runs TOTAL_BYTES HEADER_INDEX...
# Fills PR_DIFF_RUN_END and PR_DIFF_RUN_LENGTH, one pair per run of adjacent blocks, from the header offsets in PR_DIFF_HDR_OFFSET.
# A block runs from its header to the next header, or to TOTAL_BYTES for the last; `head -c END FILE | tail -c LENGTH` copies a run.
# The indices must ascend. Offsets from grep -b are 0-based, so LENGTH is END minus the run's first offset.
_pr_diff_block_runs() {
  local total=$1 index start end previous=-2 run_start=0
  shift
  PR_DIFF_RUN_END=() PR_DIFF_RUN_LENGTH=()
  for index in "$@"; do
    start=${PR_DIFF_HDR_OFFSET[index]}
    if [ $((index + 1)) -lt "${#PR_DIFF_HDR_OFFSET[@]}" ]; then
      end=${PR_DIFF_HDR_OFFSET[index + 1]}
    else
      end=$total
    fi
    if [ "$index" -eq $((previous + 1)) ]; then
      PR_DIFF_RUN_END[${#PR_DIFF_RUN_END[@]} - 1]=$end
      PR_DIFF_RUN_LENGTH[${#PR_DIFF_RUN_LENGTH[@]} - 1]=$((end - run_start))
    else
      run_start=$start
      PR_DIFF_RUN_END+=("$end")
      PR_DIFF_RUN_LENGTH+=("$((end - start))")
    fi
    previous=$index
  done
}

# ---------------------------------------------------------------- listing digest

# _pr_diff_emit_raw_rows_without_new_blob_ids
# Prints the tracked rows of the PR_DIFF_ROW_* arrays, NUL-terminated field by field, with the new-side blob id replaced by zeros of its length.
# Under --working-tree git reports that id as zeros until its first diff refreshes a stat-dirty index, so the id would change between the two calls.
_pr_diff_emit_raw_rows_without_new_blob_ids() {
  local index
  for ((index = 0; index < ${#PR_DIFF_ROW_STATUS[@]}; index++)); do
    if [ "${PR_DIFF_ROW_STATUS[index]}" = '?' ]; then
      continue
    fi
    printf '%s\0' "${PR_DIFF_ROW_OLD_MODE[index]}" "${PR_DIFF_ROW_NEW_MODE[index]}" "${PR_DIFF_ROW_OLD_BLOB[index]}" \
      "${PR_DIFF_ROW_NEW_BLOB[index]//[0-9a-f]/0}" "${PR_DIFF_ROW_STATUS[index]}" "${PR_DIFF_ROW_OLD_PATH[index]}" "${PR_DIFF_ROW_PATH[index]}"
  done
}

# _pr_diff_listing_id MODE BASE_SHA HEAD_SHA RAW_FILE UNTRACKED_FILE
# Sets PR_DIFF_RESULT to the sha256 of the five inputs, streamed from the files and never through command substitution, which drops NULs.
# Under MODE working-tree the raw rows come from the parsed PR_DIFF_ROW_* arrays with their new-side blob ids zeroed, and RAW_FILE is not read.
# A path is never empty, so a doubled NUL marks each boundary and distinct inputs cannot collide.
# Returns non-zero when an input file cannot be read, since a digest of a truncated input would name a different listing.
_pr_diff_listing_id() {
  local digest_line
  digest_line=$({
    printf '%s\0' "$1" "$2" "$3"
    printf '\0'
    if [ "$1" = working-tree ]; then
      _pr_diff_emit_raw_rows_without_new_blob_ids
    else
      cat -- "$4" || exit 1
    fi
    printf '\0'
    cat -- "$5" || exit 1
    printf '\0'
  } | sha256sum) || return $?
  PR_DIFF_RESULT=${digest_line%% *}
  [ -n "$PR_DIFF_RESULT" ]
}
