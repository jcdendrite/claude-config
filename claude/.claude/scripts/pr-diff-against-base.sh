#!/usr/bin/env bash
# Prints the cumulative PR-vs-default-branch diff /ready-for-review's step 3
# reviews to stdout: the git diff-tree patch from the merge-base of origin/<base-branch> and HEAD to HEAD.
# Usage: pr-diff-against-base.sh [--staged] [--record] [--diff-file]
#        pr-diff-against-base.sh [--staged | --working-tree | --expect-diff <path>]
#          --context-files <suffix> (--list | [--listing <id>] -- <lane>...)
# --staged prints `git diff --cached` instead of the merge-base diff,
# skipping the gh pr view/merge-base resolution entirely.
# --staged cannot combine with --record: the cumulative-review subject
# must be the cumulative diff.
# Combined with --diff-file, --staged writes to its own
# <config-dir>/code-review-diff-markers/<repo-hash>.<session-id>, distinct
# from --diff-file's default directory below.
# --record additionally records the diff as the subject
# `~/.claude/scripts/marker.sh write cumulative-review` later reads, at
# <config-dir>/cumulative-review-subject-markers/<repo-hash>.<session-id> --
# see docs/design-decisions.md §44 and §50.
# --diff-file additionally writes the diff to
# <config-dir>/cumulative-review-diff-markers/<repo-hash>.<session-id> and
# announces the path on stderr as `DIFF_FILE: <path>`, for a reviewer with
# no Bash to Read directly. See docs/scripts.md for the current caller
# list. Both flags may be combined. `marker.sh deactivate ready-for-review`
# removes both artifacts.
# --context-files writes per-spawn lane inputs under agent-reviews/ and prints only token lines on stderr; see docs/scripts.md.
set -euo pipefail

# shellcheck source=../hooks/_lib.sh
. "$(dirname "$0")/../hooks/_lib.sh"

# Resolves the PR's base branch into BASE_REF, from gh or else the default-branch guess (BASE_REF_GUESSED=1).
# Prints nothing: each mode words its own stderr line.
# gh exits nonzero for "no PR open yet" and for auth/network failure alike.
_resolve_base_ref() {
  BASE_REF_GUESSED=0
  # 15 s matches the outer cap `_lib_cumulative_diff_hash` puts on this script.
  # It is a local budget with no vendor spec.
  if BASE_REF=$(_lib_gh 15 pr view --json baseRefName --jq .baseRefName 2>/dev/null); then
    return 0
  fi
  if BASE_REF=$(_lib_default_branch_or_guess "$PWD"); then
    BASE_REF_GUESSED=1
    return 0
  fi
  return 1
}

# Fills CUMULATIVE_DIFF_ARGV with the command that prints the cumulative diff from merge-base "$1" to HEAD.
# Default mode and the --context-files --expect-diff recompute both run it.
# Diff config and GIT_DIFF_OPTS cannot separate the two runs, because both read the caller's config and the command pins GIT_DIFF_OPTS itself.
# A caller-set name in _LIB_GIT_REPOSITORY_SELECTING_ENV_VARS (see _context_prepare) can halt the call at DIFF_MISMATCH, because the handed file then describes other content.
# The command uses plumbing `git diff-tree`, so porcelain-only diff config
# (e.g. diff.relative, diff.submodule, diff.context) does not change its output.
# Observed on git 2.56.0, and pinned by the tests in test_pr_diff_against_base.py.
# See docs/design-decisions/cumulative-review-marker-keyed-on-normalized-diff.md for the residual inputs.
# GIT_DIFF_OPTS=-u3 fixes the context width at git's default of 3 lines: plumbing still reads GIT_DIFF_OPTS,
# so only setting the variable itself pins it.
# --ignore-submodules=none keeps a .gitmodules `ignore` setting from dropping a gitlink change.
# --no-color and --no-ext-diff are defense in depth: plumbing emits neither color nor external-diff output.
# The trailing `--` ends revision parsing, so git skips its revision-versus-filename ambiguity check.
# Without it, a file named `HEAD` in the cwd makes git exit 128 ("ambiguous argument").
# The leading `env` makes the array a real program, which `_lib_capped_for` needs for `timeout`.
_cumulative_diff_argv() {
  CUMULATIVE_DIFF_ARGV=(env GIT_DIFF_OPTS=-u3 git diff-tree -r -p -M --no-color --no-ext-diff --ignore-submodules=none "$1" HEAD --)
}

# ---------------------------------------------------------------- --context-files mode
# The functions down to the argument loop belong to this mode; the other modes never call them.

CONTEXT_STEP=''         # the step the EXIT trap names when a command fails under set -e
CONTEXT_EXIT_KIND=''    # deliberate (the script's own exit 2 or 3), halted (a halt printed its line) or signal
CONTEXT_TMP_BASE=''
CONTEXT_TMP_DIR=''
CONTEXT_LANE_TEMPS=()   # lane output temps under agent-reviews/, removed by recorded path
CONTEXT_MOVED_FILES=()  # final files already moved into place, removed again unless the call completes
CONTEXT_COMPLETED=0     # 1 only once every file is in place and every token is printed
BASE_REF=''
BASE_REF_GUESSED=0

# Runs on every exit. A command that failed under set -e exits 1 and shows its own status; the script's own exits 2 and 3 and the signal exits are kept.
# shellcheck disable=SC2329 # invoked indirectly via the trap registered in the --context-files branch below, which shellcheck's static analysis doesn't follow.
_context_exit_trap() {
  local exit_code=$? leftover
  set +e
  if [ "$exit_code" -ne 0 ] && [ -z "$CONTEXT_EXIT_KIND" ]; then
    _pr_diff_halt_line "$CONTEXT_STEP" "$exit_code" "" >&2
    exit_code=1
  fi
  if [ "$CONTEXT_COMPLETED" -ne 1 ]; then
    for leftover in "${CONTEXT_MOVED_FILES[@]}"; do
      rm -f -- "$leftover" || :
    done
  fi
  for leftover in "${CONTEXT_LANE_TEMPS[@]}"; do
    rm -f -- "$leftover" || :
  done
  if [ -n "$CONTEXT_TMP_DIR" ]; then
    case $CONTEXT_TMP_DIR in
      "$CONTEXT_TMP_BASE"/pr-diff-context.*) rm -rf -- "$CONTEXT_TMP_DIR" || : ;;
    esac
  fi
  exit "$exit_code"
}

_context_halt() {
  _pr_diff_halt_line "$1" "$2" "${3-}" >&2
  CONTEXT_EXIT_KIND=halted
  exit 1
}

_context_usage_error() {
  printf 'pr-diff-against-base.sh: %s\n' "$1" >&2
  CONTEXT_EXIT_KIND=deliberate
  exit 2
}

# Fills the lane arrays and CONTEXT_SOURCE.
_context_validate_args() {
  local lane_arg agent seen_agents=' ' source_count
  if [ -n "$CONTEXT_DUPLICATE_FLAG" ]; then
    _context_usage_error "$CONTEXT_DUPLICATE_FLAG was given more than once"
  fi
  if [ -n "$CONTEXT_BAD_VALUE_FLAG" ]; then
    _context_usage_error "$CONTEXT_BAD_VALUE_FLAG needs a value that does not start with -"
  fi
  source_count=$((STAGED + CONTEXT_WORKING_TREE + CONTEXT_EXPECT_DIFF_SEEN))
  if [ "$source_count" -gt 1 ]; then
    _context_usage_error "--staged, --working-tree and --expect-diff are alternatives; give at most one"
  fi
  if [ "$RECORD" -eq 1 ] || [ "$WRITE_DIFF_FILE" -eq 1 ]; then
    _context_usage_error "--context-files cannot combine with --record or --diff-file"
  fi
  if ! _pr_diff_valid_suffix "$CONTEXT_SUFFIX"; then
    _context_usage_error "--context-files needs a suffix matching ^[0-9]{1,20}-[A-Za-z0-9-]{0,20}\$"
  fi
  if [ "$CONTEXT_LIST" -eq 1 ]; then
    if [ "$CONTEXT_DASHDASH" -eq 1 ] || [ "$CONTEXT_LISTING_SEEN" -eq 1 ]; then
      _context_usage_error "--list takes no --listing and no lane"
    fi
  elif [ "${#CONTEXT_LANE_ARGS[@]}" -eq 0 ]; then
    _context_usage_error "--context-files without --list needs at least one lane after --"
  fi
  if [ "$CONTEXT_LISTING_SEEN" -eq 1 ] && ! _pr_diff_valid_listing_id "$CONTEXT_LISTING_ID"; then
    _context_usage_error "--listing needs 64 lower-case hexadecimal digits"
  fi
  LANE_AGENTS=() LANE_FORMS=() LANE_NUMBERS=()
  for lane_arg in "${CONTEXT_LANE_ARGS[@]}"; do
    if ! _pr_diff_parse_lane "$lane_arg"; then
      _context_usage_error "$PR_DIFF_USAGE_ERROR"
    fi
    agent=$PR_DIFF_LANE_AGENT
    case $seen_agents in
      *" $agent "*) _context_usage_error "agent $agent has more than one lane argument" ;;
    esac
    seen_agents+="$agent "
    LANE_AGENTS+=("$agent")
    LANE_FORMS+=("$PR_DIFF_LANE_FORM")
    LANE_NUMBERS+=("$PR_DIFF_LANE_NUMBERS")
    if [ "$PR_DIFF_LANE_FORM" = listed ] && [ "$CONTEXT_LISTING_SEEN" -eq 0 ]; then
      _context_usage_error "a listed lane needs --listing <id> from the --list call"
    fi
  done
  if [ "$STAGED" -eq 1 ]; then
    CONTEXT_SOURCE=staged
    CONTEXT_MODE_NAME=staged
  elif [ "$CONTEXT_WORKING_TREE" -eq 1 ]; then
    CONTEXT_SOURCE=working-tree
    CONTEXT_MODE_NAME=working-tree
  elif [ "$CONTEXT_EXPECT_DIFF_SEEN" -eq 1 ]; then
    CONTEXT_SOURCE=expect-diff
    CONTEXT_MODE_NAME=cumulative
    case $CONTEXT_EXPECT_DIFF in
      /*) ;;
      *) CONTEXT_EXPECT_DIFF="$PWD/$CONTEXT_EXPECT_DIFF" ;;
    esac
  else
    CONTEXT_SOURCE=cumulative
    CONTEXT_MODE_NAME=cumulative
  fi
}

_context_prepare() {
  local physical_tmp_base='' physical_root=''
  # Unsets every name in _LIB_GIT_REPOSITORY_SELECTING_ENV_VARS, which _lib.sh defines.
  # Command-scope config stays, so the recompute reads the config default mode read.
  # A true-valued pathspec variable makes git check-ignore exit 128.
  # GIT_DIFF_OPTS overrides the pinned -U3.
  unset "${_LIB_GIT_REPOSITORY_SELECTING_ENV_VARS[@]}" GIT_LITERAL_PATHSPECS GIT_GLOB_PATHSPECS GIT_NOGLOB_PATHSPECS GIT_ICASE_PATHSPECS GIT_DIFF_OPTS
  if [ "$CONTEXT_SOURCE" = cumulative ] || [ "$CONTEXT_SOURCE" = expect-diff ]; then
    CONTEXT_STEP='base-resolution'
    if ! _resolve_base_ref; then
      _context_halt base-resolution 1 "gh pr view failed and no default branch resolved from origin"
    fi
  fi
  # Diff and path bytes are not guaranteed valid in the user's locale, so byte-processing tools run under LC_ALL=C.
  export LC_ALL=C
  CONTEXT_STEP='repo-root'
  REPO_ROOT=$(_lib_repo_root)
  cd "$REPO_ROOT"
  CONTEXT_STEP='temp-dir'
  CONTEXT_TMP_BASE=${TMPDIR:-/tmp}
  CONTEXT_TMP_BASE=${CONTEXT_TMP_BASE%/}
  readonly CONTEXT_TMP_BASE
  # The private directory holds withheld bytes, so a base that is relative or resolves to the repository would put them under it.
  case $CONTEXT_TMP_BASE in
    /*) ;;
    *) _context_halt temp-dir 1 "TMPDIR must be an absolute path" ;;
  esac
  physical_tmp_base=$(cd -P -- "$CONTEXT_TMP_BASE" 2>/dev/null && pwd -P) || :
  physical_root=$(cd -P -- "$REPO_ROOT" && pwd -P)
  case $physical_tmp_base in
    "$physical_root" | "$physical_root"/*) _context_halt temp-dir 1 "TMPDIR must not be inside the repository" ;;
  esac
  # Holds every file that can contain withheld bytes, so none is ever written under the repository.
  CONTEXT_TMP_DIR=$(mktemp -d "$CONTEXT_TMP_BASE/pr-diff-context.XXXXXX")
  readonly CONTEXT_TMP_DIR
}

# Exits 0 from here with DIFF_EMPTY when nothing changed.
_context_collect_rows() {
  local head_status=0 recompute_status=0 handed_status=0 handed='' recomputed=''
  DIFF_PIN=(
    --no-color --no-ext-diff --no-textconv --no-relative -M
    --src-prefix=a/ --dst-prefix=b/ --submodule=short --ignore-submodules=none
  )
  CONTEXT_STEP='rev-parse'
  HEAD_SHA=$(_lib_capped git rev-parse --verify --quiet HEAD 2>/dev/null) || head_status=$?
  if [ "$head_status" -eq 1 ]; then
    _context_halt rev-parse 1 "no commit yet"
  elif [ "$head_status" -ne 0 ]; then
    exit "$head_status"
  fi
  case $CONTEXT_SOURCE in
    staged)
      REVISION=--cached
      BASE_SHA=$HEAD_SHA
      HEAD_FIELD=-
      ;;
    working-tree)
      REVISION=HEAD
      BASE_SHA=$HEAD_SHA
      HEAD_FIELD=-
      ;;
    *)
      CONTEXT_STEP='merge-base'
      MERGE_BASE=$(_lib_capped_for "$CONTEXT_GIT_CALL_SECONDS" git merge-base "origin/$BASE_REF" HEAD 2>/dev/null)
      REVISION="$MERGE_BASE...HEAD"
      BASE_SHA=$MERGE_BASE
      HEAD_FIELD=$HEAD_SHA
      ;;
  esac
  CONTEXT_STEP='untracked-list'
  UNTRACKED_FILE=$(mktemp "$CONTEXT_TMP_DIR/untracked.XXXXXX")
  if [ "$CONTEXT_SOURCE" = working-tree ]; then
    _lib_capped_for "$CONTEXT_DIFF_CALL_SECONDS" git ls-files --others --exclude-standard -z > "$UNTRACKED_FILE" 2>/dev/null
  fi
  CONTEXT_STEP='core-raw'
  RAW_FILE=$(mktemp "$CONTEXT_TMP_DIR/raw.XXXXXX")
  _lib_capped_for "$CONTEXT_DIFF_CALL_SECONDS" git -c core.quotePath=false diff --raw -z --no-abbrev "${DIFF_PIN[@]}" "$REVISION" -- > "$RAW_FILE" 2>/dev/null
  if [ "$CONTEXT_LIST" -eq 0 ]; then
    CONTEXT_STEP='core-numstat'
    NUMSTAT_FILE=$(mktemp "$CONTEXT_TMP_DIR/numstat.XXXXXX")
    _lib_capped_for "$CONTEXT_DIFF_CALL_SECONDS" git -c core.quotePath=false diff --numstat -z "${DIFF_PIN[@]}" "$REVISION" -- > "$NUMSTAT_FILE" 2>/dev/null
  fi
  if [ "$CONTEXT_SOURCE" = expect-diff ]; then
    CONTEXT_STEP='expect-diff'
    # This runs default mode's own command and capture, so no diff setting separates the two and both drop the same NULs.
    _cumulative_diff_argv "$MERGE_BASE"
    { recomputed=$(_lib_capped_for "$CONTEXT_DIFF_CALL_SECONDS" "${CUMULATIVE_DIFF_ARGV[@]}" 2>/dev/null); } 2>/dev/null || recompute_status=$?
    if [ "$recompute_status" -ne 0 ]; then
      _context_halt expect-diff "$recompute_status" ""
    fi
    # A failed redirection inside $(< file) aborts the shell whatever follows it, so readability is checked first.
    if [ ! -f "$CONTEXT_EXPECT_DIFF" ] || [ ! -r "$CONTEXT_EXPECT_DIFF" ]; then
      _context_halt expect-diff 1 "could not read the handed diff file"
    fi
    { handed=$(< "$CONTEXT_EXPECT_DIFF"); } 2>/dev/null || handed_status=$?
    if [ "$handed_status" -ne 0 ]; then
      _context_halt expect-diff 1 "could not read the handed diff file"
    fi
    if ! _pr_diff_normalized_equal "$handed" "$recomputed"; then
      _context_halt expect-diff 1 "DIFF_MISMATCH: the handed diff file differs from the current cumulative diff; re-run /ready-for-review step 3"
    fi
  fi
  CONTEXT_STEP='core-raw'
  if ! _pr_diff_parse_raw "$RAW_FILE"; then
    _context_halt core-raw 1 "$PR_DIFF_PARSE_ERROR"
  fi
  if [ "$CONTEXT_SOURCE" = working-tree ]; then
    _pr_diff_append_untracked "$UNTRACKED_FILE"
  fi
  ROW_COUNT=${#PR_DIFF_ROW_STATUS[@]}
  if [ "$ROW_COUNT" -eq 0 ]; then
    case $CONTEXT_SOURCE in
      staged) printf 'DIFF_EMPTY: no staged changes\n' >&2 ;;
      working-tree) printf 'DIFF_EMPTY: no changes, tracked or untracked\n' >&2 ;;
      *) printf 'DIFF_EMPTY: no changes against the merge base\n' >&2 ;;
    esac
    exit 0
  fi
}

_context_build_listing() {
  local index old_path
  CONTEXT_STEP='status-check'
  if ! _pr_diff_check_status_grammar; then
    _pr_diff_index_path "${PR_DIFF_ROW_PATH[PR_DIFF_BAD_ROW]}"
    _context_halt status-check 1 "unsupported status ${PR_DIFF_ROW_STATUS[PR_DIFF_BAD_ROW]} for $PR_DIFF_RESULT"
  fi
  if [ "$CONTEXT_LIST" -eq 0 ]; then
    CONTEXT_STEP='core-numstat'
    if ! _pr_diff_parse_numstat "$NUMSTAT_FILE"; then
      _context_halt core-numstat 1 "$PR_DIFF_PARSE_ERROR"
    fi
    CONTEXT_STEP='lane-diff'
    if ! _pr_diff_classify_rows; then
      _context_halt lane-diff 1 "$PR_DIFF_PARSE_ERROR"
    fi
  fi
  _pr_diff_guard_file_path
  _pr_diff_read_guard_globs "$PR_DIFF_RESULT"
  _pr_diff_load_data_guard
  PR_DIFF_SCAN_TEXT=()
  SCAN_RECORD_ROW=()
  for ((index = 0; index < ROW_COUNT; index++)); do
    PR_DIFF_SCAN_TEXT+=("${PR_DIFF_ROW_PATH[index]}")
    SCAN_RECORD_ROW+=("$index")
    old_path=${PR_DIFF_ROW_OLD_PATH[index]}
    if [ "$old_path" != "${PR_DIFF_ROW_PATH[index]}" ]; then
      PR_DIFF_SCAN_TEXT+=("$old_path")
      SCAN_RECORD_ROW+=("$index")
    fi
  done
  _pr_diff_scan_withheld "$REPO_ROOT"
  ROW_WITHHELD=()
  for ((index = 0; index < ROW_COUNT; index++)); do
    ROW_WITHHELD[index]=0
  done
  for ((index = 0; index < ${#PR_DIFF_SCAN_HIT[@]}; index++)); do
    if [ "${PR_DIFF_SCAN_HIT[index]}" -eq 1 ]; then
      ROW_WITHHELD[SCAN_RECORD_ROW[index]]=1
    fi
  done
  CONTEXT_STEP='listing-id'
  _pr_diff_listing_id "$CONTEXT_MODE_NAME" "$BASE_SHA" "$HEAD_FIELD" "$RAW_FILE" "$UNTRACKED_FILE"
  LISTING_ID=$PR_DIFF_RESULT
}

# The listing has no row ceiling, so a listing of thousands of rows is the point to add one.
_context_print_listing() {
  local index withheld_field listing_text
  listing_text="LISTING: $LISTING_ID $ROW_COUNT"$'\n'
  for ((index = 0; index < ROW_COUNT; index++)); do
    withheld_field=-
    if [ "${ROW_WITHHELD[index]}" -eq 1 ]; then
      withheld_field=withheld
    fi
    _pr_diff_index_path "${PR_DIFF_ROW_PATH[index]}"
    listing_text+="CHANGED_PATH: $((index + 1))"$'\t'"${PR_DIFF_ROW_STATUS[index]}"$'\t'"$withheld_field"$'\t'"$PR_DIFF_RESULT"
    case ${PR_DIFF_ROW_STATUS[index]} in
      R* | C*)
        _pr_diff_index_path "${PR_DIFF_ROW_OLD_PATH[index]}"
        listing_text+=$'\t'"$PR_DIFF_RESULT"
        ;;
    esac
    listing_text+=$'\n'
  done
  printf '%s' "$listing_text" >&2
}

_context_check_lanes_against_listing() {
  local lane number index
  if [ "$CONTEXT_LISTING_SEEN" -eq 1 ] && [ "$CONTEXT_LISTING_ID" != "$LISTING_ID" ]; then
    printf 'LISTING_STALE: the --listing id did not match the id recomputed from the changed paths; they moved between the calls, or the id was mistyped\n' >&2
    CONTEXT_EXIT_KIND=deliberate
    exit 2
  fi
  for ((lane = 0; lane < ${#LANE_AGENTS[@]}; lane++)); do
    if [ "${LANE_FORMS[lane]}" != listed ]; then
      continue
    fi
    for number in ${LANE_NUMBERS[lane]}; do
      if [ "$number" -gt "$ROW_COUNT" ]; then
        _context_usage_error "lane ${LANE_AGENTS[lane]} names number $number, outside 1..$ROW_COUNT"
      fi
    done
    if [ "${LANE_AGENTS[lane]}" = ciso-reviewer ]; then
      for ((index = 0; index < ROW_COUNT; index++)); do
        if [ "${ROW_WITHHELD[index]}" -eq 1 ]; then
          case " ${LANE_NUMBERS[lane]} " in
            *" $((index + 1)) "*) ;;
            *) _context_usage_error "the listed ciso-reviewer lane omits withheld number $((index + 1))" ;;
          esac
        fi
      done
    fi
  done
}

_context_prepare_output_dir() {
  local lane kind ignore_status stem final_path
  local -a kinds=(index diff)
  CONTEXT_STEP='ignore-check'
  for ((lane = 0; lane < ${#LANE_FORMS[@]}; lane++)); do
    if [ "${LANE_FORMS[lane]}" != diff ]; then
      kinds+=(base history)
      break
    fi
  done
  stem="${LANE_AGENTS[0]}-$CONTEXT_SUFFIX"
  for kind in "${kinds[@]}"; do
    ignore_status=0
    _lib_path_ignore_status "$REPO_ROOT" "agent-reviews/$stem.$kind" || ignore_status=$?
    if [ "$ignore_status" -eq 1 ]; then
      _pr_diff_index_path "$REPO_ROOT/agent-reviews/$stem.$kind"
      printf 'CONTEXT_NOT_IGNORED: %s; add agent-reviews/ to .gitignore or .git/info/exclude, and remove any rule that un-ignores files under it; %s\n' "$PR_DIFF_RESULT" "$PR_DIFF_HALT_POINTER" >&2
      CONTEXT_EXIT_KIND=deliberate
      exit 3
    elif [ "$ignore_status" -ne 0 ]; then
      _context_halt ignore-check "$ignore_status" ""
    fi
  done
  CONTEXT_STEP='output-dir'
  OUTPUT_DIR="$REPO_ROOT/agent-reviews"
  if [ ! -e "$OUTPUT_DIR" ] && [ ! -L "$OUTPUT_DIR" ]; then
    mkdir -m 0700 "$OUTPUT_DIR" 2>/dev/null || :
  fi
  if [ -L "$OUTPUT_DIR" ] || [ ! -d "$OUTPUT_DIR" ]; then
    _pr_diff_index_path "$OUTPUT_DIR"
    _context_halt output-dir 1 "agent-reviews/ is not a real directory: $PR_DIFF_RESULT"
  fi
  for ((lane = 0; lane < ${#LANE_AGENTS[@]}; lane++)); do
    for kind in index diff base history; do
      final_path="$OUTPUT_DIR/${LANE_AGENTS[lane]}-$CONTEXT_SUFFIX.$kind"
      if [ -L "$final_path" ] || { [ -e "$final_path" ] && [ ! -f "$final_path" ]; }; then
        _pr_diff_index_path "$final_path"
        _context_halt output-dir 1 "an output target is a symlink or not a regular file: $PR_DIFF_RESULT"
      fi
    done
  done
}

_context_slice_lane_diffs() {
  local grep_status=0 lane row reason preamble run header_index shared_file found_file
  local -a keep=() keep_indices=()
  CONTEXT_STEP='lane-diff'
  shared_file=$(mktemp "$CONTEXT_TMP_DIR/shared.XXXXXX")
  # No pathspec, so no pathspec form or GIT_*_PATHSPECS variable changes what it selects.
  _lib_capped_for "$CONTEXT_DIFF_CALL_SECONDS" git -c core.quotePath=false diff "${DIFF_PIN[@]}" -p --full-index -U3 "$REVISION" -- > "$shared_file" 2>/dev/null
  CONTEXT_STEP='header-check'
  found_file=$(mktemp "$CONTEXT_TMP_DIR/headers.XXXXXX")
  SHARED_BYTES=$(wc -c < "$shared_file")
  SHARED_BYTES=${SHARED_BYTES//[[:space:]]/}
  LC_ALL=C grep -a -b '^diff --git ' "$shared_file" > "$found_file" || grep_status=$?
  if [ "$grep_status" -gt 1 ]; then
    exit "$grep_status"
  fi
  _pr_diff_build_expected_headers
  # Every diff --git header in the shared diff must match the index rows, so a block the rows do not account for halts the round before any lane file is written.
  if ! _pr_diff_check_headers "$found_file" "$SHARED_BYTES"; then
    _context_halt header-check 1 "$PR_DIFF_HEADER_DETAIL"
  fi
  CONTEXT_STEP='lane-slice'
  # A lane .diff copies its blocks uncapped, so a shared diff of many megabytes is the point to cap it.
  LANE_DIFF_TEMPS=()
  for ((lane = 0; lane < ${#LANE_AGENTS[@]}; lane++)); do
    LANE_DIFF_TEMPS[lane]=$(mktemp "$OUTPUT_DIR/.${LANE_AGENTS[lane]}-$CONTEXT_SUFFIX.diff.XXXXXX")
    CONTEXT_LANE_TEMPS+=("${LANE_DIFF_TEMPS[lane]}")
    _pr_diff_lane_row_indices "${LANE_FORMS[lane]}" "${LANE_NUMBERS[lane]}" "$ROW_COUNT"
    preamble=''
    keep=()
    for row in "${PR_DIFF_LANE_ROWS[@]}"; do
      reason=''
      if [ "${ROW_WITHHELD[row]}" -eq 1 ]; then
        reason=withheld
      elif [ "${PR_DIFF_ROW_STATUS[row]}" = '?' ]; then
        reason=untracked
      fi
      if [ -n "$reason" ]; then
        _pr_diff_index_path "${PR_DIFF_ROW_PATH[row]}"
        preamble+="# omitted $reason: $PR_DIFF_RESULT"$'\n'
      else
        for header_index in ${PR_DIFF_ROW_HDRS[row]}; do
          keep[header_index]=1
        done
      fi
    done
    if [ -n "$preamble" ]; then
      preamble+=$'\n'
    fi
    printf '%s' "$preamble" > "${LANE_DIFF_TEMPS[lane]}"
    keep_indices=("${!keep[@]}")
    if [ "${#keep_indices[@]}" -gt 0 ]; then
      _pr_diff_block_runs "$SHARED_BYTES" "${keep_indices[@]}"
      # Each run re-reads the shared diff from its start, so slicing time grows with runs times diff size.
      for ((run = 0; run < ${#PR_DIFF_RUN_END[@]}; run++)); do
        head -c "${PR_DIFF_RUN_END[run]}" "$shared_file" | tail -c "${PR_DIFF_RUN_LENGTH[run]}" >> "${LANE_DIFF_TEMPS[lane]}"
      done
    fi
  done
  rm -f -- "$shared_file" "$found_file"
}

_context_enrich_base() {
  local row=$1 size_text='' size_status=0 blob_status=0 base_temp blob
  blob=${PR_DIFF_ROW_OLD_BLOB[row]}
  if ! _pr_diff_enrichment_has_time; then
    BASE_STATE[row]=skipped
    return 0
  fi
  size_text=$(_lib_capped_for "$CONTEXT_GIT_CALL_SECONDS" git cat-file -s "$blob" 2>/dev/null) || size_status=$?
  case $size_text in
    '' | *[!0-9]*) size_status=1 ;;
  esac
  if [ "$size_status" -ne 0 ]; then
    BASE_STATE[row]=unavailable
    return 0
  fi
  if [ "$size_text" -gt "$BASE_BYTES_PER_PATH" ]; then
    BASE_STATE[row]=over-cap
    return 0
  fi
  if ! _pr_diff_enrichment_has_time; then
    BASE_STATE[row]=skipped
    return 0
  fi
  base_temp=$(mktemp "$CONTEXT_TMP_DIR/base.XXXXXX")
  _lib_capped_for "$CONTEXT_GIT_CALL_SECONDS" git cat-file blob "$blob" > "$base_temp" 2>/dev/null || blob_status=$?
  if [ "$blob_status" -ne 0 ]; then
    rm -f -- "$base_temp"
    BASE_STATE[row]=unavailable
    return 0
  fi
  _pr_diff_count_lines "$base_temp" "$((BASE_BYTES_PER_PATH + 1))"
  BASE_STATE[row]=ok
  BASE_FILE[row]=$base_temp
  BASE_LINES[row]=$PR_DIFF_LINES
  BASE_BYTES[row]=$PR_DIFF_BYTES
  BASE_NOEOL[row]=$PR_DIFF_NOEOL
}

# Runs a capped `git ARGS...` with stdout written to FILE, never past HISTORY_BYTES_PER_PATH + 1 bytes, so a long history cannot fill the temp directory.
# Sets HISTORY_LOG_STATUS to 0, to 141 when the output outgrew the cap and git died of SIGPIPE, or to the failing command's own status.
_context_log_into_file() {
  local file=$1
  shift
  HISTORY_LOG_STATUS=0
  _lib_capped_for "$CONTEXT_GIT_CALL_SECONDS" git "$@" 2>/dev/null | head -c "$((HISTORY_BYTES_PER_PATH + 1))" > "$file" || HISTORY_LOG_STATUS=$?
}

_context_enrich_history() {
  local row=$1 history_temp commit_temp pre_image_path history_exceeds=0 commit_exceeds=0
  local -a log_pin=(
    -c core.quotePath=false --literal-pathspecs log -n "$HISTORY_COMMITS_PER_PATH" --no-merges
    --no-color --no-ext-diff --no-textconv --no-show-signature --follow
  )
  pre_image_path=${PR_DIFF_ROW_OLD_PATH[row]}
  if ! _pr_diff_enrichment_has_time; then
    HISTORY_STATE[row]=skipped
    return 0
  fi
  history_temp=$(mktemp "$CONTEXT_TMP_DIR/history/hist.XXXXXX")
  _context_log_into_file "$history_temp" "${log_pin[@]}" -p --src-prefix=a/ --dst-prefix=b/ --submodule=short --ignore-submodules=none -U3 \
    '--format=commit %H%n%ad %s%n%n%b' --date=short "$BASE_SHA" -- "$pre_image_path"
  if [ "$HISTORY_LOG_STATUS" -eq 141 ]; then
    history_exceeds=1
  elif [ "$HISTORY_LOG_STATUS" -ne 0 ]; then
    rm -f -- "$history_temp"
    HISTORY_STATE[row]=unavailable
    return 0
  else
    _pr_diff_count_lines "$history_temp" "$HISTORY_BYTES_PER_PATH"
    history_exceeds=$PR_DIFF_EXCEEDS
  fi
  HISTORY_STATE[row]=ok
  if [ "$history_exceeds" -eq 1 ]; then
    if ! _pr_diff_enrichment_has_time; then
      rm -f -- "$history_temp"
      HISTORY_STATE[row]=skipped
      return 0
    fi
    commit_temp=$(mktemp "$CONTEXT_TMP_DIR/history/hist.XXXXXX")
    _context_log_into_file "$commit_temp" "${log_pin[@]}" '--format=commit %H%n%ad %s' --date=short "$BASE_SHA" -- "$pre_image_path"
    rm -f -- "$history_temp"
    if [ "$HISTORY_LOG_STATUS" -eq 141 ]; then
      commit_exceeds=1
    elif [ "$HISTORY_LOG_STATUS" -ne 0 ]; then
      rm -f -- "$commit_temp"
      HISTORY_STATE[row]=unavailable
      return 0
    else
      _pr_diff_count_lines "$commit_temp" "$HISTORY_BYTES_PER_PATH"
      commit_exceeds=$PR_DIFF_EXCEEDS
    fi
    if [ "$commit_exceeds" -eq 1 ]; then
      rm -f -- "$commit_temp"
      HISTORY_STATE[row]=over-cap
      return 0
    fi
    history_temp=$commit_temp
    HISTORY_STATE[row]=commits-only
  fi
  HISTORY_FILE[row]=$history_temp
  HISTORY_LINES[row]=$PR_DIFF_LINES
  HISTORY_BYTES[row]=$PR_DIFF_BYTES
  HISTORY_NOEOL[row]=$PR_DIFF_NOEOL
  HISTORY_FILE_ROW[$history_temp]=$row
}

# The grep runs inside the history directory and prints "<name>:<line>" records, which any grep dialect does; a temp name holds no colon, so the first colon ends the name.
# A header scan that fails marks every history body unavailable, since no header was checked. A scan that finds no header has still completed, so grep's status 1 is success.
# This runs outside the enrichment clock, and its time grows with the number of header lines, not rows.
_context_withhold_history_bodies() {
  local grep_record history_file header_line row index scan_status=0 headers_file
  local -a record_row=()
  if [ "${#HISTORY_FILE_ROW[@]}" -eq 0 ]; then
    return 0
  fi
  headers_file=$(mktemp "$CONTEXT_TMP_DIR/history-headers.XXXXXX")
  { (cd -- "$CONTEXT_TMP_DIR/history" || exit 2; LC_ALL=C grep -a -H -E '^(diff --git |--- |\+\+\+ |rename from |rename to )' -- hist.* || [ "$?" -eq 1 ]) > "$headers_file"; } 2>/dev/null || scan_status=$?
  if [ "$scan_status" -ne 0 ]; then
    for history_file in "${!HISTORY_FILE_ROW[@]}"; do
      row=${HISTORY_FILE_ROW[$history_file]}
      HISTORY_STATE[row]=unavailable
    done
    return 0
  fi
  PR_DIFF_SCAN_TEXT=()
  while IFS= read -r grep_record; do
    history_file="$CONTEXT_TMP_DIR/history/${grep_record%%:*}"
    header_line=${grep_record#*:}
    row=${HISTORY_FILE_ROW[$history_file]-}
    if [ -z "$row" ]; then
      continue
    fi
    _pr_diff_history_header_names "$header_line"
    for index in "${!PR_DIFF_HEADER_NAMES[@]}"; do
      PR_DIFF_SCAN_TEXT+=("${PR_DIFF_HEADER_NAMES[index]}")
      record_row+=("$row")
    done
  done < "$headers_file"
  _pr_diff_scan_withheld "$REPO_ROOT"
  for index in "${!PR_DIFF_SCAN_HIT[@]}"; do
    if [ "${PR_DIFF_SCAN_HIT[index]}" -eq 1 ]; then
      HISTORY_STATE[record_row[index]]=unavailable
    fi
  done
}

_context_enrich() {
  local row status old_mode eligible_base
  CONTEXT_STEP='enrichment'
  BASE_STATE=() BASE_FILE=() BASE_LINES=() BASE_BYTES=() BASE_NOEOL=()
  HISTORY_STATE=() HISTORY_FILE=() HISTORY_LINES=() HISTORY_BYTES=() HISTORY_NOEOL=()
  declare -gA HISTORY_FILE_ROW=()
  ENRICH_START=''
  PR_DIFF_LANE_FORM_LIST=("${LANE_FORMS[@]}")
  PR_DIFF_LANE_NUMBERS_LIST=("${LANE_NUMBERS[@]}")
  _pr_diff_enrichment_order
  mkdir -m 0700 "$CONTEXT_TMP_DIR/history"
  for row in "${PR_DIFF_ENRICH_ORDER[@]}"; do
    status=${PR_DIFF_ROW_STATUS[row]}
    if [ "${ROW_WITHHELD[row]}" -eq 1 ] || [ "$status" = '?' ] || [ "$status" = A ]; then
      continue
    fi
    old_mode=${PR_DIFF_ROW_OLD_MODE[row]}
    eligible_base=1
    case " ${PR_DIFF_ROW_NOTES[row]} " in
      *" binary "*) eligible_base=0 ;;
    esac
    if [ "$old_mode" = 160000 ]; then
      eligible_base=0
    fi
    if [ "$eligible_base" -eq 1 ]; then
      _context_enrich_base "$row"
    fi
    _context_enrich_history "$row"
  done
  _context_withhold_history_bodies
}

_context_lane_row_bodies() {
  local row=$1 state
  ROW_BASE_RANGE=- ROW_HISTORY_RANGE=- ROW_BODY_NOTES=''
  state=${BASE_STATE[row]-}
  case $state in
    ok)
      if [ "$LANE_BYTES" -ge "$CONTEXT_BYTES_PER_LANE" ]; then
        ROW_BODY_NOTES+=' over-cap'
        DEGRADED_COUNT=$((DEGRADED_COUNT + 1))
      else
        cat -- "${BASE_FILE[row]}" >> "$LANE_BASE_TEMP"
        if [ "${BASE_NOEOL[row]}" -eq 1 ]; then
          printf '\n' >> "$LANE_BASE_TEMP"
          ROW_BODY_NOTES+=' no-eol'
        fi
        _pr_diff_next_range BASE_CURSOR "${BASE_LINES[row]}"
        ROW_BASE_RANGE=$PR_DIFF_RESULT
        LANE_BYTES=$((LANE_BYTES + BASE_BYTES[row]))
      fi
      ;;
    over-cap | unavailable | skipped)
      ROW_BODY_NOTES+=" $state"
      DEGRADED_COUNT=$((DEGRADED_COUNT + 1))
      ;;
  esac
  state=${HISTORY_STATE[row]-}
  case $state in
    ok | commits-only)
      if [ "$LANE_BYTES" -ge "$CONTEXT_BYTES_PER_LANE" ]; then
        ROW_BODY_NOTES+=' over-cap'
        DEGRADED_COUNT=$((DEGRADED_COUNT + 1))
      else
        cat -- "${HISTORY_FILE[row]}" >> "$LANE_HISTORY_TEMP"
        if [ "${HISTORY_NOEOL[row]}" -eq 1 ]; then
          printf '\n' >> "$LANE_HISTORY_TEMP"
          ROW_BODY_NOTES+=' no-eol'
        fi
        _pr_diff_next_range HISTORY_CURSOR "${HISTORY_LINES[row]}"
        ROW_HISTORY_RANGE=$PR_DIFF_RESULT
        LANE_BYTES=$((LANE_BYTES + HISTORY_BYTES[row]))
        if [ "$state" = commits-only ]; then
          ROW_BODY_NOTES+=' commits-only'
          DEGRADED_COUNT=$((DEGRADED_COUNT + 1))
        fi
      fi
      ;;
    over-cap | unavailable | skipped)
      ROW_BODY_NOTES+=" $state"
      DEGRADED_COUNT=$((DEGRADED_COUNT + 1))
      ;;
  esac
}

_context_compose_lane_files() {
  local lane row stem form member_count next_member in_lane lane_label generated
  local rows_text header_text row_path row_old_path notes_extra lane_field
  local base_field history_field diff_path_field
  CONTEXT_STEP='lane-slice'
  LANE_INDEX_TEMPS=() LANE_BASE_TEMPS=() LANE_HISTORY_TEMPS=()
  LANE_ROW_LINES='' LANE_ROW_TOTAL=0 DEGRADED_COUNT=0
  TZ=UTC0 printf -v generated '%(%Y-%m-%dT%H:%M:%SZ)T' -1
  for ((lane = 0; lane < ${#LANE_AGENTS[@]}; lane++)); do
    stem="${LANE_AGENTS[lane]}-$CONTEXT_SUFFIX"
    form=${LANE_FORMS[lane]}
    # shellcheck disable=SC2034 # the two cursors are advanced through namerefs inside _pr_diff_next_range.
    LANE_BYTES=0 BASE_CURSOR=1 HISTORY_CURSOR=1 LANE_BASE_TEMP='' LANE_HISTORY_TEMP=''
    if [ "$form" != diff ]; then
      LANE_BASE_TEMP=$(mktemp "$OUTPUT_DIR/.$stem.base.XXXXXX")
      CONTEXT_LANE_TEMPS+=("$LANE_BASE_TEMP")
      LANE_HISTORY_TEMP=$(mktemp "$OUTPUT_DIR/.$stem.history.XXXXXX")
      CONTEXT_LANE_TEMPS+=("$LANE_HISTORY_TEMP")
    fi
    LANE_INDEX_TEMPS[lane]=$(mktemp "$OUTPUT_DIR/.$stem.index.XXXXXX")
    CONTEXT_LANE_TEMPS+=("${LANE_INDEX_TEMPS[lane]}")
    LANE_BASE_TEMPS[lane]=$LANE_BASE_TEMP
    LANE_HISTORY_TEMPS[lane]=$LANE_HISTORY_TEMP
    _pr_diff_lane_row_indices "$form" "${LANE_NUMBERS[lane]}" "$ROW_COUNT"
    member_count=${#PR_DIFF_LANE_ROWS[@]}
    next_member=0
    rows_text=''
    for ((row = 0; row < ROW_COUNT; row++)); do
      in_lane=0
      if [ "$next_member" -lt "$member_count" ] && [ "${PR_DIFF_LANE_ROWS[next_member]}" -eq "$row" ]; then
        in_lane=1
        next_member=$((next_member + 1))
      fi
      ROW_BASE_RANGE=- ROW_HISTORY_RANGE=- ROW_BODY_NOTES=''
      if [ "$in_lane" -eq 1 ] && [ "$form" != diff ]; then
        _context_lane_row_bodies "$row"
      fi
      notes_extra=$ROW_BODY_NOTES
      if [ "${ROW_WITHHELD[row]}" -eq 1 ]; then
        notes_extra+=' withheld'
      fi
      _pr_diff_canonical_notes "${PR_DIFF_ROW_NOTES[row]} $notes_extra"
      lane_field=-
      if [ "$in_lane" -eq 1 ]; then
        lane_field=lane
      fi
      rows_text+="${PR_DIFF_ROW_STATUS[row]}"$'\t'"$lane_field"$'\t'"$ROW_BASE_RANGE"$'\t'"$ROW_HISTORY_RANGE"$'\t'"$PR_DIFF_RESULT"$'\t'
      _pr_diff_index_path "${PR_DIFF_ROW_PATH[row]}"
      row_path=$PR_DIFF_RESULT
      rows_text+=$row_path
      case ${PR_DIFF_ROW_STATUS[row]} in
        R* | C*)
          _pr_diff_index_path "${PR_DIFF_ROW_OLD_PATH[row]}"
          row_old_path=$PR_DIFF_RESULT
          rows_text+=$'\t'"$row_old_path"
          ;;
      esac
      rows_text+=$'\n'
      if [ "$in_lane" -eq 1 ] && [ "$form" = listed ]; then
        LANE_ROW_LINES+="LANE_ROW: ${LANE_AGENTS[lane]} $((row + 1)) $row_path"$'\n'
        LANE_ROW_TOTAL=$((LANE_ROW_TOTAL + 1))
      fi
    done
    case $form in
      whole) lane_label=whole-diff ;;
      diff) lane_label=diff-only ;;
      *) lane_label=listed ;;
    esac
    _pr_diff_index_path "$REPO_ROOT"
    header_text="lane-index: 1"$'\n'"mode: $CONTEXT_MODE_NAME"$'\n'"root: $PR_DIFF_RESULT"$'\n'
    header_text+="base: $BASE_SHA"$'\n'"head: $HEAD_FIELD"$'\n'"generated: $generated"$'\n'"lane: $lane_label"$'\n'
    _pr_diff_index_path "$OUTPUT_DIR/$stem.diff"
    diff_path_field=$PR_DIFF_RESULT
    base_field=- history_field=-
    if [ "$form" != diff ]; then
      _pr_diff_index_path "$OUTPUT_DIR/$stem.base"
      base_field=$PR_DIFF_RESULT
      _pr_diff_index_path "$OUTPUT_DIR/$stem.history"
      history_field=$PR_DIFF_RESULT
    fi
    header_text+="diff-file: $diff_path_field"$'\n'"base-file: $base_field"$'\n'"history-file: $history_field"$'\n'$'\n'
    printf '%s%s' "$header_text" "$rows_text" > "${LANE_INDEX_TEMPS[lane]}"
  done
}

# The index moves last, and each moved file is recorded so a later failure removes it again.
_context_move_into_place() {
  local lane stem final_path
  CONTEXT_STEP='write'
  for ((lane = 0; lane < ${#LANE_AGENTS[@]}; lane++)); do
    stem="${LANE_AGENTS[lane]}-$CONTEXT_SUFFIX"
    if [ "${LANE_FORMS[lane]}" != diff ]; then
      final_path="$OUTPUT_DIR/$stem.base"
      mv -- "${LANE_BASE_TEMPS[lane]}" "$final_path"
      CONTEXT_MOVED_FILES+=("$final_path")
      final_path="$OUTPUT_DIR/$stem.history"
      mv -- "${LANE_HISTORY_TEMPS[lane]}" "$final_path"
      CONTEXT_MOVED_FILES+=("$final_path")
    fi
    final_path="$OUTPUT_DIR/$stem.diff"
    mv -- "${LANE_DIFF_TEMPS[lane]}" "$final_path"
    CONTEXT_MOVED_FILES+=("$final_path")
    final_path="$OUTPUT_DIR/$stem.index"
    mv -- "${LANE_INDEX_TEMPS[lane]}" "$final_path"
    CONTEXT_MOVED_FILES+=("$final_path")
  done
}

# Every token line is printed after every move succeeded, so its presence proves the files exist.
_context_print_tokens() {
  local lane stem token_text=''
  if [ "$DEGRADED_COUNT" -gt 0 ]; then
    token_text+="CONTEXT_DEGRADED: $DEGRADED_COUNT"$'\n'
  fi
  if [ "$BASE_REF_GUESSED" -eq 1 ]; then
    token_text+="BASE_GUESSED: $BASE_REF"$'\n'
  fi
  token_text+="LANE_ROW_COUNT: $LANE_ROW_TOTAL"$'\n'
  for ((lane = 0; lane < ${#LANE_AGENTS[@]}; lane++)); do
    stem="${LANE_AGENTS[lane]}-$CONTEXT_SUFFIX"
    _pr_diff_index_path "$OUTPUT_DIR/$stem.index"
    token_text+="LANE_INDEX: ${LANE_AGENTS[lane]} $PR_DIFF_RESULT"$'\n'
    _pr_diff_index_path "$OUTPUT_DIR/$stem.diff"
    token_text+="LANE_DIFF: ${LANE_AGENTS[lane]} $PR_DIFF_RESULT"$'\n'
  done
  token_text+=$LANE_ROW_LINES
  printf '%s' "$token_text" >&2
}

_context_files_main() {
  _context_validate_args
  _context_prepare
  _context_collect_rows
  _context_build_listing
  if [ "$CONTEXT_LIST" -eq 1 ]; then
    _context_print_listing
    exit 0
  fi
  _context_check_lanes_against_listing
  _context_prepare_output_dir
  _context_slice_lane_diffs
  _context_enrich
  _context_compose_lane_files
  _context_move_into_place
  _context_print_tokens
  CONTEXT_COMPLETED=1
  exit 0
}

STAGED=0
RECORD=0
WRITE_DIFF_FILE=0
TMP_FILE=""
DIFF_TMP=""
CONTEXT_FILES=0
CONTEXT_SUFFIX=''
CONTEXT_LIST=0
CONTEXT_WORKING_TREE=0
CONTEXT_EXPECT_DIFF=''
CONTEXT_EXPECT_DIFF_SEEN=0
CONTEXT_LISTING_ID=''
CONTEXT_LISTING_SEEN=0
CONTEXT_DASHDASH=0
CONTEXT_LANE_ARGS=()
CONTEXT_DUPLICATE_FLAG=''
CONTEXT_BAD_VALUE_FLAG=''
CONTEXT_ONLY_FLAG=''
STAGED_SEEN_COUNT=0
while [ "$#" -gt 0 ]; do
  case "$1" in
    --staged)
      STAGED=1
      STAGED_SEEN_COUNT=$((STAGED_SEEN_COUNT + 1))
      shift
      ;;
    --record)
      RECORD=1
      shift
      ;;
    --diff-file)
      WRITE_DIFF_FILE=1
      shift
      ;;
    --context-files)
      if [ "$CONTEXT_FILES" -eq 1 ] && [ -z "$CONTEXT_DUPLICATE_FLAG" ]; then
        CONTEXT_DUPLICATE_FLAG=--context-files
      fi
      CONTEXT_FILES=1
      if [ "$#" -lt 2 ]; then
        CONTEXT_BAD_VALUE_FLAG=--context-files
        shift
      elif [[ $2 == -* ]]; then
        CONTEXT_BAD_VALUE_FLAG=--context-files
        shift 2
      else
        CONTEXT_SUFFIX=$2
        shift 2
      fi
      ;;
    --working-tree)
      CONTEXT_ONLY_FLAG=${CONTEXT_ONLY_FLAG:---working-tree}
      if [ "$CONTEXT_WORKING_TREE" -eq 1 ] && [ -z "$CONTEXT_DUPLICATE_FLAG" ]; then
        CONTEXT_DUPLICATE_FLAG=--working-tree
      fi
      CONTEXT_WORKING_TREE=1
      shift
      ;;
    --expect-diff)
      CONTEXT_ONLY_FLAG=${CONTEXT_ONLY_FLAG:---expect-diff}
      if [ "$CONTEXT_EXPECT_DIFF_SEEN" -eq 1 ] && [ -z "$CONTEXT_DUPLICATE_FLAG" ]; then
        CONTEXT_DUPLICATE_FLAG=--expect-diff
      fi
      CONTEXT_EXPECT_DIFF_SEEN=1
      if [ "$#" -lt 2 ]; then
        CONTEXT_BAD_VALUE_FLAG=--expect-diff
        shift
      elif [[ $2 == -* ]]; then
        CONTEXT_BAD_VALUE_FLAG=--expect-diff
        shift 2
      else
        CONTEXT_EXPECT_DIFF=$2
        shift 2
      fi
      ;;
    --list)
      CONTEXT_ONLY_FLAG=${CONTEXT_ONLY_FLAG:---list}
      if [ "$CONTEXT_LIST" -eq 1 ] && [ -z "$CONTEXT_DUPLICATE_FLAG" ]; then
        CONTEXT_DUPLICATE_FLAG=--list
      fi
      CONTEXT_LIST=1
      shift
      ;;
    --listing)
      CONTEXT_ONLY_FLAG=${CONTEXT_ONLY_FLAG:---listing}
      if [ "$CONTEXT_LISTING_SEEN" -eq 1 ] && [ -z "$CONTEXT_DUPLICATE_FLAG" ]; then
        CONTEXT_DUPLICATE_FLAG=--listing
      fi
      CONTEXT_LISTING_SEEN=1
      if [ "$#" -lt 2 ]; then
        CONTEXT_BAD_VALUE_FLAG=--listing
        shift
      elif [[ $2 == -* ]]; then
        CONTEXT_BAD_VALUE_FLAG=--listing
        shift 2
      else
        CONTEXT_LISTING_ID=$2
        shift 2
      fi
      ;;
    --)
      CONTEXT_ONLY_FLAG=${CONTEXT_ONLY_FLAG:---}
      CONTEXT_DASHDASH=1
      shift
      CONTEXT_LANE_ARGS=("$@")
      set --
      ;;
    *)
      printf 'pr-diff-against-base.sh: unknown argument %s (supports --staged, --record, --diff-file, --context-files, --working-tree, --expect-diff, --list, --listing)\n' "$1" >&2
      exit 2
      ;;
  esac
done

if [ "$CONTEXT_FILES" -eq 1 ]; then
  # An inherited xtrace would print every diff held in a variable to stderr.
  set +x
  trap _context_exit_trap EXIT
  # 143 and 130 are 128 plus the signal number (TERM 15, INT 2).
  trap 'CONTEXT_EXIT_KIND=signal; exit 143' TERM
  trap 'CONTEXT_EXIT_KIND=signal; exit 130' INT
  if [ "$STAGED_SEEN_COUNT" -gt 1 ] && [ -z "$CONTEXT_DUPLICATE_FLAG" ]; then
    CONTEXT_DUPLICATE_FLAG=--staged
  fi
  context_lib_path="$(dirname "$0")/_pr-diff-lib.sh"
  if [ ! -r "$context_lib_path" ]; then
    printf 'pr-diff-against-base.sh: could not source _pr-diff-lib.sh (run ./install.sh to pick up script files this update added)\n' >&2
    CONTEXT_EXIT_KIND=deliberate
    exit 2
  fi
  # shellcheck source=_pr-diff-lib.sh
  . "$context_lib_path"
  _context_files_main
fi

if [ -n "$CONTEXT_ONLY_FLAG" ]; then
  printf 'pr-diff-against-base.sh: %s requires --context-files\n' "$CONTEXT_ONLY_FLAG" >&2
  exit 2
fi

if [ "$STAGED" -eq 1 ] && [ "$RECORD" -eq 1 ]; then
  printf 'pr-diff-against-base.sh: --staged cannot be combined with --record (the cumulative-review subject must be the cumulative diff)\n' >&2
  exit 2
fi

if [ "$STAGED" -eq 1 ]; then
  DIFF_MARKER_DIR_NAME="code-review-diff-markers"
  if ! DIFF_TEXT=$(_lib_capped git diff --cached 2>/dev/null); then
    printf 'pr-diff-against-base.sh: --staged could not compute the staged diff\n' >&2
    exit 1
  fi
  if [ -z "$DIFF_TEXT" ]; then
    printf 'DIFF_EMPTY: no staged changes\n' >&2
    exit 0
  fi
else
  DIFF_MARKER_DIR_NAME="cumulative-review-diff-markers"
  if ! _resolve_base_ref; then
    printf 'pr-diff-against-base.sh: gh pr view failed and no default branch resolved from origin\n' >&2
    exit 1
  fi
  if [ "$BASE_REF_GUESSED" -eq 1 ]; then
    printf 'pr-diff-against-base.sh: gh pr view failed; defaulting base to %s\n' "$BASE_REF" >&2
  fi

  if ! MERGE_BASE=$(git merge-base "origin/$BASE_REF" HEAD 2>/dev/null); then
    printf 'pr-diff-against-base.sh: could not resolve merge-base against origin/%s\n' "$BASE_REF" >&2
    exit 1
  fi

  _cumulative_diff_argv "$MERGE_BASE"
  if ! DIFF_TEXT=$("${CUMULATIVE_DIFF_ARGV[@]}" 2>/dev/null); then
    printf 'pr-diff-against-base.sh: could not compute the diff against %s\n' "$MERGE_BASE" >&2
    exit 1
  fi
fi

# Command substitution strips trailing newlines; the printf below restores exactly one.
# Stdout matches git's own output apart from trailing newlines and NUL bytes, which command substitution drops.
printf '%s\n' "$DIFF_TEXT"

# Everything below writes optional artifacts (--record, --diff-file). Both
# always run after the diff has already reached stdout, so a
# CONFIG_DIR/REPO_HASH/mkdir failure here never costs the caller the diff it
# asked for, and never changes this script's exit code -- only stderr
# reports it.

# Single combined EXIT trap for both blocks below: a second `trap ... EXIT`
# call would silently overwrite this one, per
# claude/.claude/rules/shell-script-conventions.md.
# shellcheck disable=SC2329 # invoked indirectly via the trap registered below, which shellcheck's static analysis doesn't follow.
_cleanup_diff_temp_files() {
  # Captures and restores $? explicitly: under `set -e`, an EXIT trap whose
  # last command is a false `[ -n ... ]` test would otherwise overwrite the
  # script's real exit code with that test's own failure status.
  local exit_code=$?
  [ -n "$TMP_FILE" ] && rm -f "$TMP_FILE"
  [ -n "$DIFF_TMP" ] && rm -f "$DIFF_TMP"
  return "$exit_code"
}
trap _cleanup_diff_temp_files EXIT

if [ "$RECORD" -eq 1 ]; then
  if REPO_ROOT=$(_lib_repo_root) && CONFIG_DIR=$(_lib_config_dir) && SESSION_ID=$(_lib_resolve_session_id); then
    if REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT"); then
      SUBJECT_DIR="$CONFIG_DIR/cumulative-review-subject-markers"
      # Session-id-suffixed, matching every completion marker kind's own
      # <repo-hash>.<session-id> keying, so two sessions recording in the same
      # worktree don't overwrite or consume each other's subject.
      SUBJECT_NAME="$REPO_HASH.$SESSION_ID"
      if mkdir -p "$SUBJECT_DIR" 2>/dev/null; then
        if TMP_FILE=$(mktemp "$SUBJECT_DIR/.$SUBJECT_NAME.XXXXXX" 2>/dev/null); then
          # No trailing newline here (unlike the stdout printf above): marker.sh
          # judges emptiness on this file's canonicalized $(cat ...) text, which
          # strips trailing newlines the same way regardless of what's written.
          if printf '%s' "$DIFF_TEXT" > "$TMP_FILE" && mv "$TMP_FILE" "$SUBJECT_DIR/$SUBJECT_NAME"; then
            TMP_FILE=""
          else
            printf 'pr-diff-against-base.sh: --record could not write the subject file; subject not recorded.\n' >&2
          fi
        else
          printf 'pr-diff-against-base.sh: --record could not create a temp file in %s; subject not recorded.\n' "$SUBJECT_DIR" >&2
        fi
      else
        printf 'pr-diff-against-base.sh: --record could not create %s; subject not recorded.\n' "$SUBJECT_DIR" >&2
      fi
    else
      printf 'pr-diff-against-base.sh: --record could not compute the repo hash; subject not recorded.\n' >&2
    fi
  else
    printf 'pr-diff-against-base.sh: --record could not resolve the repo root, config directory, or session id; subject not recorded.\n' >&2
  fi
fi

if [ "$WRITE_DIFF_FILE" -eq 1 ]; then
  if REPO_ROOT=$(_lib_repo_root) && CONFIG_DIR=$(_lib_config_dir) && SESSION_ID=$(_lib_resolve_session_id); then
    if REPO_HASH=$(_marker_lib_repo_hash "$REPO_ROOT"); then
      DIFF_DIR="$CONFIG_DIR/$DIFF_MARKER_DIR_NAME"
      DIFF_NAME="$REPO_HASH.$SESSION_ID"
      if mkdir -p "$DIFF_DIR" 2>/dev/null; then
        if DIFF_TMP=$(mktemp "$DIFF_DIR/.$DIFF_NAME.XXXXXX" 2>/dev/null); then
          # Unlike --record's subject, this file keeps stdout's trailing
          # newline: it has no hashing consumer to disagree with.
          # mktemp+mv is atomic for one writer at a time per session; a
          # concurrent --diff-file call within the same session is unsupported,
          # since ready-for-review step 4 calls this once per gate pass.
          if printf '%s\n' "$DIFF_TEXT" > "$DIFF_TMP" && mv "$DIFF_TMP" "$DIFF_DIR/$DIFF_NAME"; then
            DIFF_TMP=""
            # Printed only after the mv succeeds, so the line's presence is
            # proof the file exists.
            printf 'DIFF_FILE: %s\n' "$DIFF_DIR/$DIFF_NAME" >&2
          else
            printf 'pr-diff-against-base.sh: --diff-file could not write the diff file; diff file not written.\n' >&2
          fi
        else
          printf 'pr-diff-against-base.sh: --diff-file could not create a temp file in %s; diff file not written.\n' "$DIFF_DIR" >&2
        fi
      else
        printf 'pr-diff-against-base.sh: --diff-file could not create %s; diff file not written.\n' "$DIFF_DIR" >&2
      fi
    else
      printf 'pr-diff-against-base.sh: --diff-file could not compute the repo hash; diff file not written.\n' >&2
    fi
  else
    printf 'pr-diff-against-base.sh: --diff-file could not resolve the repo root, config directory, or session id; diff file not written.\n' >&2
  fi
fi

exit 0
