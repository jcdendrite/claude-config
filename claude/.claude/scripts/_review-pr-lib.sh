#!/bin/bash
# _review-pr-lib.sh — shared checks for review-pr-acquire.sh,
# review-pr-checkout.sh and review-pr-diff.sh.
#
# Sourced by those scripts after hooks/_lib.sh, whose _lib_jq,
# _lib_resolve_claude_pid and _lib_valid_session_id_component it calls; not
# executable on its own.
#
# Provides:
#   REVIEW_PR_FILE_NAMES_JQ_FILTER  — the `gh api --jq` filter that prints each file name as one JSON string per line
#   REVIEW_PR_COMMIT_SHAS_JQ_FILTER — the `gh api --jq` filter that prints each commit SHA on its own line
#   REVIEW_PR_REVIEWS_JQ_FILTER     — the `gh api --jq` filter that keeps each review with a non-empty body as one {id, author, state, body} object per line
#   REVIEW_PR_INLINE_COMMENTS_JQ_FILTER — the `gh api --jq` filter that prints each inline comment as one {author, path, line, body} object per line
#   review_pr_rest_changed_files    — the REST payload's changed_files count, validated as an integer
#   review_pr_decode_file_names     — the @json stream decoded into one JSON array
#   review_pr_file_count_matches    — a decoded listing's length equals the expected count
#   REVIEW_PR_AUDIT_CLEAN_DOCUMENT  — the stdout the audit prints for a file list with no match, without its trailing newline
#   review_pr_audit_verdict         — the audit's exit status and stdout classified as clean, stop or failed
#   REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT — the most characters of the audit's stdout a failed-verdict message echoes
#   review_pr_audit_stdout_excerpt  — the audit's stdout cut to its first line and to that limit, for a failed-verdict message
#   REVIEW_PR_AUDIT_MATCH_LINE_LIMIT — the most matches a stop message lists
#   REVIEW_PR_AUDIT_PATH_ECHO_LIMIT — the most characters of one matched path a stop message echoes
#   review_pr_audit_match_lines     — the audit's stop document as one escaped, bounded line per match, for a stop message
#   review_pr_audit_match_report    — review_pr_audit_match_lines, or a fixed line with the match count when the document cannot be formatted
#   review_pr_gh_status_description — "timed out" for a gh exit status of 124, else "failed (exit N)"
#   review_pr_resolve_session_and_pid — sets SESSION_ID and CLAUDE_PID for the scripts that record provenance
#
# Every function except review_pr_resolve_session_and_pid takes its input as an
# argument, prints its result on stdout where it has one, returns 0 (true) or 1
# (false) (a function that only prints returns 0), and performs no gh call and
# no filesystem access. Error text and exit codes stay with the caller.
# review_pr_resolve_session_and_pid is the one exception. It reads the session
# file under the config dir, spawns ps, prints caller-parameterised text on
# stderr, and sets SESSION_ID and CLAUDE_PID as globals in the caller.

# One JSON string per line keeps a name holding a raw newline on a single line.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_FILE_NAMES_JQ_FILTER='.[].filename | @json'

# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_COMMIT_SHAS_JQ_FILTER='.[].sha'

# A review with an empty body (inline comments only) is dropped. A deleted account's null `.user` gives a null author.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_REVIEWS_JQ_FILTER='.[] | select(.body != "") | {id, author: .user.login, state, body}'

# A deleted account's null `.user` gives a null author, and a null `line` stays null.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_INLINE_COMMENTS_JQ_FILTER='.[] | {author: .user.login, path, line, body}'

# The audit's `json.dumps({"stop": False, "matches": []})` output, which it prints for a file list with no match.
REVIEW_PR_AUDIT_CLEAN_DOCUMENT='{"stop": false, "matches": []}'

# A display bound for one terminal message. No protocol or vendor limit backs the value.
REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT=200

# Display bounds for one stop message. No protocol or vendor limit backs either value.
# The worst-case message stays under the 30,000-byte Bash output truncation threshold (claude-skills/skills/subagent-delegation/REFERENCES.md).
REVIEW_PR_AUDIT_MATCH_LINE_LIMIT=20
REVIEW_PR_AUDIT_PATH_ECHO_LIMIT=80

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

# review_pr_audit_verdict AUDIT_EXIT_STATUS AUDIT_STDOUT
# Prints how to read one run of audit-execution-surface.py, whose documented statuses are 0 (clean), 1 (stop) and 2 (malformed input). AUDIT_STDOUT is the `$(...)`-captured stdout, so it has no trailing newline.
# Prints `clean` only for status 0 with stdout byte-equal to REVIEW_PR_AUDIT_CLEAN_DOCUMENT.
# Prints `stop` only for status 1 with stdout whose `.stop` is true.
# Prints `failed` for everything else, such as status 2, 127 (python3 missing), a signal death, an uncaught exception, or status 0 with any other stdout, so a tooling failure is never read as a verdict.
# The status alone is not proof of a verdict: an uncaught exception also exits 1, and an empty script exits 0.
# Callers run the audit as `python3 -I`, which keeps PYTHON* variables and the user site directory out of the gate (the audit imports only json and sys).
# Callers leave the audit's stderr unredirected, so a failed run shows its own error.
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

# review_pr_audit_match_lines AUDIT_STDOUT
# Prints one `<path as a JSON string>: <reason>` line per match in the audit's stop document.
# A PR file name is attacker-chosen, so each path is cut to REVIEW_PR_AUDIT_PATH_ECHO_LIMIT characters (then marked `...[truncated]`) and printed as an ASCII-only JSON string.
# Printable ASCII passes through, including the JSON two-character forms for a newline, tab, quote and backslash (`\n`, `\t`, `\"`, `\\`). Every other character, such as another control character, a C1 control, a bidirectional override, U+2028, a zero-width character or a tag-block character, prints as `\uXXXX`, with a surrogate pair above U+FFFF.
# Matches past REVIEW_PR_AUDIT_MATCH_LINE_LIMIT are replaced by one line counting them, then one `not shown:` line per distinct reason among them, so no reason present in the document goes unnamed.
# A reason is audit-owned and stays verbatim.
# Prints nothing and returns 1 when AUDIT_STDOUT has no `.matches` array or a match entry cannot be formatted.
review_pr_audit_match_lines() {
  [[ -n "$1" ]] || return 1
  local formatted_lines
  # shellcheck disable=SC2016 # $matches, $match_limit and $path_limit are jq bindings in a single-quoted filter, and double quotes would expand them as shell variables.
  formatted_lines=$(printf '%s' "$1" | _lib_jq -r \
    --argjson match_limit "$REVIEW_PR_AUDIT_MATCH_LINE_LIMIT" \
    --argjson path_limit "$REVIEW_PR_AUDIT_PATH_ECHO_LIMIT" '
    def hex4: [(. / 4096 | floor) % 16, (. / 256 | floor) % 16, (. / 16 | floor) % 16, . % 16] | map("0123456789abcdef"[. : . + 1]) | join("");
    def unicode_escape: "\\u" + hex4;
    def escape_codepoint:
      if . > 31 and . < 127 then [.]
      elif . < 65536 then unicode_escape | explode
      else (. - 65536) as $offset
        | (55296 + ($offset / 1024 | floor) | unicode_escape) + (56320 + ($offset % 1024) | unicode_escape) | explode
      end;
    def ascii_json_string: @json | explode | map(escape_codepoint) | add | implode;
    if (.matches | type) != "array" then error("no matches array") else
      .matches as $matches
      | ($matches[:$match_limit][]
          | "\(.path | if length > $path_limit then .[:$path_limit] + "...[truncated]" else . end | ascii_json_string): \(.reason)"),
        (if ($matches | length) > $match_limit then
          "... and \($matches | length - $match_limit) more matched path(s) not shown",
          ($matches[$match_limit:] | group_by(.reason)[] | "not shown: \(.[0].reason) (\(length) path(s))")
        else empty end)
    end
  ' 2>/dev/null) || return 1
  [[ -z "$formatted_lines" ]] || printf '%s\n' "$formatted_lines"
}

# review_pr_audit_match_report AUDIT_STDOUT
# Prints review_pr_audit_match_lines' output for the audit's stop document.
# When that helper cannot format the document, prints one fixed line with the count of matches the document reports (`unknown` when it has no `.matches` array) and never echoes the document.
# Always returns 0, so a caller's stop path reaches its own exit.
review_pr_audit_match_report() {
  local match_count
  if review_pr_audit_match_lines "$1"; then
    return 0
  fi
  match_count=$(printf '%s' "$1" | _lib_jq -r 'if (.matches | type) == "array" then (.matches | length) else empty end' 2>/dev/null) || match_count=""
  [[ "$match_count" =~ ^[0-9]+$ ]] || match_count="unknown"
  printf 'the audit reported %s matched path(s), but its match list could not be formatted for display' "$match_count"
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

# review_pr_resolve_session_and_pid SCRIPT_NAME CONSEQUENCE ABORT_NOTE
# Sets the globals SESSION_ID and CLAUDE_PID from _lib_resolve_claude_pid. CLAUDE_PID is this session's own Claude Code process, the same PID every other skill's active-bypass marker stores.
# Returns 1 after printing one line on stderr when the session id cannot be resolved or is not a valid path component. SCRIPT_NAME opens that line, CONSEQUENCE (empty, or starting with " -- ") follows the reason, and ABORT_NOTE (a full sentence) ends it.
# Unlike the functions above it walks the process ancestry, so it reads $CONFIG_DIR/sessions/<pid> and spawns ps.
review_pr_resolve_session_and_pid() {
  local script_name="$1" consequence="$2" abort_note="$3" session_and_pid
  session_and_pid=$(_lib_resolve_claude_pid) || {
    echo "$script_name: could not resolve this session's id (capture-session-id.sh SessionStart hook did not run)$consequence. $abort_note" >&2
    return 1
  }
  SESSION_ID="${session_and_pid%% *}"
  CLAUDE_PID="${session_and_pid##* }"
  if ! _lib_valid_session_id_component "$SESSION_ID"; then
    echo "$script_name: resolved session id '$SESSION_ID' is not a valid path component$consequence. $abort_note" >&2
    return 1
  fi
}
