#!/bin/bash
# _review-pr-lib.sh — shared pure checks for review-pr-acquire.sh,
# review-pr-checkout.sh and review-pr-diff.sh.
#
# Sourced by those scripts after hooks/_lib.sh, whose _lib_jq it calls; not
# executable on its own.
#
# Provides:
#   REVIEW_PR_FILE_NAMES_JQ_FILTER  — the `gh api --jq` filter that prints each file name as one JSON string per line
#   review_pr_rest_changed_files    — the REST payload's changed_files count, validated as an integer
#   review_pr_decode_file_names     — the @json stream decoded into one JSON array
#   review_pr_file_count_matches    — a decoded listing's length equals the expected count
#   review_pr_file_names_line_safe  — no name holds a refused character
#   REVIEW_PR_AUDIT_CLEAN_DOCUMENT  — the stdout the audit prints for a file list with no match, without its trailing newline
#   review_pr_audit_verdict         — the audit's exit status and stdout classified as clean, stop or failed
#   REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT — the most characters of the audit's stdout a failed-verdict message echoes
#   review_pr_audit_stdout_excerpt  — the audit's stdout cut to its first line and to that limit, for a failed-verdict message
#   review_pr_gh_status_description — "timed out" for a gh exit status of 124, else "failed (exit N)"
#
# The refused characters, stated once here: every code point below 32 (C0),
# DEL (127), the C1 controls (128-159), and `^`.
#
# Every function takes its input as an argument, prints its result on stdout
# where it has one, returns 0 (true) or 1 (false) unless its own comment names
# another status (a function that only prints returns 0), and performs no gh
# call and no filesystem access. Error text and exit codes stay with the caller.

# One JSON string per line keeps a name holding a raw newline on a single line.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_FILE_NAMES_JQ_FILTER='.[].filename | @json'

# The audit's `json.dumps({"stop": False, "matches": []})` output, which it prints for a file list with no match.
REVIEW_PR_AUDIT_CLEAN_DOCUMENT='{"stop": false, "matches": []}'

# A display bound for one terminal message. No protocol or vendor limit backs the value.
REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT=200

# review_pr_rest_changed_files REST_JSON
# Prints the payload's `changed_files` when it is a non-negative integer.
# Returns 1, printing nothing, on a missing, null, non-integer or unparseable value.
review_pr_rest_changed_files() {
  local changed_files
  changed_files=$(printf '%s' "$1" | _lib_jq -r '.changed_files // empty' 2>/dev/null) || return 1
  [[ "$changed_files" =~ ^[0-9]+$ ]] || return 1
  printf '%s' "$changed_files"
}

# review_pr_decode_file_names RAW
# Prints RAW, a stream of JSON string literals, as one compact JSON array.
# Returns 1 when RAW is not a valid stream of JSON values.
review_pr_decode_file_names() {
  printf '%s' "$1" | _lib_jq -c -s '.' 2>/dev/null || return 1
}

# review_pr_file_count_matches FILES_JSON EXPECTED
# Prints FILES_JSON's length. Returns 0 only when that length equals EXPECTED.
# Returns 1 on a mismatch, and returns 1 printing nothing when FILES_JSON is not a JSON array.
review_pr_file_count_matches() {
  local listed_count
  listed_count=$(printf '%s' "$1" | _lib_jq -r 'if type == "array" then length else empty end' 2>/dev/null) || return 1
  [[ -n "$listed_count" ]] || return 1
  printf '%s' "$listed_count"
  [[ "$listed_count" == "$2" ]]
}

# review_pr_file_names_line_safe FILES_JSON
# Returns 0 when no string element of FILES_JSON holds a refused character (see the header).
# Returns 1 when one does.
# Returns 2 when that could not be decided: a jq failure, output other than `true` or `false`, or input that is not a JSON array.
# Models gh 2.100.0's behavior, not independently re-verified: a JSON-escaped control character reaches `--jq` as caret notation, so a `^` may stand for one.
review_pr_file_names_line_safe() {
  local has_refused_character
  has_refused_character=$(printf '%s' "$1" | _lib_jq -r 'if type == "array" then any(.[]; type == "string" and (explode | any(. < 32 or . == 127 or (. >= 128 and . < 160) or . == 94))) else empty end' 2>/dev/null) || return 2
  case "$has_refused_character" in
    false) return 0 ;;
    true) return 1 ;;
    *) return 2 ;;
  esac
}

# review_pr_audit_verdict AUDIT_EXIT_STATUS AUDIT_STDOUT
# Prints how to read one run of audit-execution-surface.py, whose documented statuses are 0 (clean), 1 (stop) and 2 (malformed input). AUDIT_STDOUT is the `$(...)`-captured stdout, so it has no trailing newline.
# Prints `clean` only for status 0 with stdout byte-equal to REVIEW_PR_AUDIT_CLEAN_DOCUMENT.
# Prints `stop` only for status 1 with stdout whose `.stop` is true.
# Prints `failed` for everything else, such as status 2, 127 (python3 missing), a signal death, an uncaught exception, or status 0 with any other stdout, so a tooling failure is never read as a verdict.
review_pr_audit_verdict() {
  if [[ "$1" == "0" && "$2" == "$REVIEW_PR_AUDIT_CLEAN_DOCUMENT" ]]; then
    printf 'clean'
  elif [[ "$1" == "1" ]] && printf '%s' "$2" | _lib_jq -e '.stop == true' >/dev/null 2>&1; then
    printf 'stop'
  else
    printf 'failed'
  fi
}

# review_pr_audit_stdout_excerpt AUDIT_STDOUT
# Prints AUDIT_STDOUT's first line. A first line longer than REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT characters is cut to that length and followed by `...[truncated]`.
review_pr_audit_stdout_excerpt() {
  local first_line=${1%%$'\n'*}
  if (( ${#first_line} > REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT )); then
    printf '%s...[truncated]' "${first_line:0:REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT}"
  else
    printf '%s' "$first_line"
  fi
}

# review_pr_gh_status_description GH_EXIT_STATUS
# Prints "timed out" for 124, the status GNU timeout exits with when its cap fires, else "failed (exit N)".
# Callers pass the status of a gh call run through _lib_gh, and never echo gh's own stderr.
# A cap kill that surfaces as 137 or 143 (BusyBox reports 143) reads as "failed (exit N)", since those are also a child's own signal-death statuses.
review_pr_gh_status_description() {
  if [[ "$1" == "124" ]]; then
    printf 'timed out'
  else
    printf 'failed (exit %s)' "$1"
  fi
}
