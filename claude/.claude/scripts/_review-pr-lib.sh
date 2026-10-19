#!/usr/bin/env bash
# _review-pr-lib.sh — shared checks for review-pr-acquire.sh,
# review-pr-checkout.sh, review-pr-diff.sh and review-pr-post.sh.
#
# Sourced by those scripts after hooks/_lib.sh, whose _lib_jq,
# _lib_resolve_claude_pid, _lib_valid_session_id_component,
# _lib_status_consistent_with_cap_kill and _lib_case_insensitive_ne it calls;
# not executable on its own.
#
# Provides:
#   REVIEW_PR_FILE_NAMES_JQ_FILTER  — the `gh api --jq` filter that prints each file name as one JSON string per line
#   REVIEW_PR_COMMIT_SHAS_JQ_FILTER — the `gh api --jq` filter that prints each commit SHA on its own line
#   REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS — the cap in seconds on one `git fetch` of a PR ref, a base commit or a default branch
#   REVIEW_PR_REVIEWS_JQ_FILTER     — the `gh api --jq` filter that keeps each review with a non-empty body as one {id, author, state, body} object per line
#   REVIEW_PR_INLINE_COMMENTS_JQ_FILTER — the `gh api --jq` filter that prints each inline comment as one {author, path, line, body} object per line
#   review_pr_rest_changed_files    — the REST payload's changed_files count, validated as an integer
#   review_pr_decode_file_names     — the @json stream decoded into one JSON array
#   review_pr_file_count_matches    — a decoded listing's length equals the expected count
#   review_pr_is_full_hex_oid       — a value is a full SHA-1 or SHA-256 lowercase hex object name
#   REVIEW_PR_BASE_IS_DEFAULT_BRANCH_TOKEN — the fixed stdout line the checkout and diff scripts print when the PR's base branch is the repository's default branch
#   review_pr_base_is_default_branch — a PR's base branch name equals the repository's default branch name, both non-empty
#   review_pr_nul_names_to_json     — NUL-terminated names on stdin decoded into one JSON array
#   review_pr_bare_count            — a command's printed count trimmed of surrounding whitespace and validated as an integer
#   review_pr_ascii_json_string     — an attacker-chosen string, optionally cut to a limit, printed as one ASCII-only JSON string, for a message
#   REVIEW_PR_AUDIT_CLEAN_DOCUMENT  — the stdout the audit prints for a file list with no match, without its trailing newline
#   review_pr_audit_verdict         — the audit's exit status and stdout classified as clean, stop or failed
#   REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT — the most characters of the audit's stdout a failed-verdict message echoes
#   review_pr_audit_stdout_excerpt  — the audit's stdout cut to its first line and to that limit, for a failed-verdict message
#   REVIEW_PR_AUDIT_MATCH_LINE_LIMIT — the most matches a stop message lists
#   REVIEW_PR_AUDIT_PATH_ECHO_LIMIT — the most characters of one matched path a stop message echoes
#   review_pr_audit_match_lines     — the audit's stop document as one escaped, bounded line per match, for a stop message
#   review_pr_audit_match_report    — review_pr_audit_match_lines, or a fixed line with the match count when the document cannot be formatted
#   review_pr_gh_status_description — "timed out" for a gh exit status of 124, else "failed (exit N)"
#   review_pr_git_status_description — "exited N", plus a cap note when N is a status a cap kill produces
#   review_pr_checks_token_source   — whether the check-status fetch uses the CI_CHECKS_GH_TOKEN override (PR url on github.com), withholds it, or runs on the ambient token
#   review_pr_author_login          — a `gh pr view --json author` response's `.author.login`, printed only when the response is exactly one object whose `.author` is an object with a string `.login`
#   review_pr_authorship            — whether a PR author's login equals the login `gh api user` returns: self, other or unknown
#   review_pr_resolve_session_and_pid — sets SESSION_ID and CLAUDE_PID for the scripts that record provenance
#   REVIEW_PR_STACK_MAX_LOOKUPS     — the most open-PR lookups one stack walk makes
#   REVIEW_PR_STACK_LOOKUP_LIMIT    — the most items one stack-walk lookup reads; a full page reads as undecided
#   REVIEW_PR_STACK_LOOKUP_TIMEOUT_SECONDS — the cap in seconds on one stack-walk lookup
#   REVIEW_PR_STACK_GH_REVIEWS_LIMIT — the most reviews `gh pr list` returns per PR
#   REVIEW_PR_STACK_TEXT_ECHO_LIMIT — the most characters of a PR title, a login or a branch name the stack document and a walk failure line echo
#   REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT — the most distinct reviews the stack document lists per ancestor
#   REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE — the fixed sentence that ends a walk failure no re-run clears
#   REVIEW_PR_STACK_GH_FAILURE_NOTE — the fixed sentences that end a walk failure caused by a gh call
#   REVIEW_PR_EXIT_STACK_UNREVIEWED — the checkout and diff scripts' exit status when an ancestor PR has no submitted review
#   review_pr_stack_lookup_verdict  — one `gh pr list --head` reply classified as none, truncated, ambiguous or an ancestor with its review summary
#   review_pr_stack_document        — the ancestor entries and root branch assembled into the stack document with its concerns and choices
#   review_pr_walk_stack            — the chain of ancestor PRs behind a PR's base branch, as a stack document or the lowest ancestor with no review
#
# Every function except review_pr_nul_names_to_json,
# review_pr_resolve_session_and_pid and review_pr_walk_stack takes its input as
# an argument, prints its result on stdout where it has one, returns 0 (true) or
# 1 (false) (a function that only prints returns 0), and performs no gh call and
# no filesystem access. Error text and exit codes stay with the caller.
# review_pr_nul_names_to_json is the stdin exception: it reads its input from
# stdin, because a NUL byte cannot travel in an argument.
# review_pr_resolve_session_and_pid is the other exception. It reads the session
# file under the config dir, spawns ps, prints caller-parameterised text on
# stderr, and sets SESSION_ID and CLAUDE_PID as globals in the caller.
# review_pr_walk_stack is the third exception. It makes gh calls through _lib_gh
# and prints one caller-prefixed line on stderr per failure. It sets no global.
# Its pure pieces, review_pr_stack_lookup_verdict and review_pr_stack_document,
# make no gh call.

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

# A git fetch is a network transfer, not a local read. No protocol or vendor limit backs the value. The value assumes a clone that already holds most of the fetched history.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_GIT_FETCH_TIMEOUT_SECONDS=30

# The fixed text that review-pr-checkout.sh and review-pr-diff.sh print on their own stdout line when the base is the default branch.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_BASE_IS_DEFAULT_BRANCH_TOKEN='BASE_IS_DEFAULT_BRANCH'

# The audit's `json.dumps({"stop": False, "matches": []})` output, which it prints for a file list with no match.
REVIEW_PR_AUDIT_CLEAN_DOCUMENT='{"stop": false, "matches": []}'

# A display bound for one terminal message. No protocol or vendor limit backs the value.
REVIEW_PR_AUDIT_STDOUT_ECHO_LIMIT=200

# Display bounds for one stop message. No protocol or vendor limit backs either value.
# The worst-case message stays under the 30,000-byte Bash output truncation threshold (claude-skills/skills/subagent-delegation/REFERENCES.md).
REVIEW_PR_AUDIT_MATCH_LINE_LIMIT=20
REVIEW_PR_AUDIT_PATH_ECHO_LIMIT=80

# jq definitions that print a string as an ASCII-only JSON string; `ascii_json_string` is the entry point, and `truncated_ascii_json_string` cuts to a character limit first.
# shellcheck disable=SC2034 # read by the functions below; export would leak it into every child process
# shellcheck disable=SC2016 # the text is jq source holding `$offset`, and double quotes would expand it as a shell variable.
REVIEW_PR_JQ_ASCII_JSON_STRING_DEFS='
    def hex4: [(. / 4096 | floor) % 16, (. / 256 | floor) % 16, (. / 16 | floor) % 16, . % 16] | map("0123456789abcdef"[. : . + 1]) | join("");
    def unicode_escape: "\\u" + hex4;
    def escape_codepoint:
      if . > 31 and . < 127 then [.]
      elif . < 65536 then unicode_escape | explode
      else (. - 65536) as $offset
        | (55296 + ($offset / 1024 | floor) | unicode_escape) + (56320 + ($offset % 1024) | unicode_escape) | explode
      end;
    def ascii_json_string: @json | explode | map(escape_codepoint) | add | implode;
    def truncated_ascii_json_string($limit): (if length > $limit then .[:$limit] + "...[truncated]" else . end) | ascii_json_string;
'

# No protocol or vendor limit backs the value. It keeps checkout's stacked worst case under the Bash tool's 600 s maximum (claude-skills/skills/review-pr/REFERENCES.md "Caps").
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_MAX_LOOKUPS=8

# The most items one lookup reads; a full page reads as undecided, since a same-repository PR past it would go unseen.
# 100 is GitHub's GraphQL page maximum, and a larger `gh pr list --limit` makes gh paginate, which would break the single-request cap below.
# The rate-limit point cost of one lookup is unmeasured and presumed fine at operator scale; re-evaluate it if REVIEW_PR_STACK_MAX_LOOKUPS is raised.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_LOOKUP_LIMIT=100

# GitHub's API documentation states that a request taking more than 10 seconds to process is terminated (docs.github.com, "Rate limits and query limits for the GraphQL API", "Timeouts"). The client cap also covers gh startup, auth and the transfer of up to 100 PRs with their reviews, and it was not measured against a full reply.
# It matches the 10 s GH_PR_VIEW_TIMEOUT_SECONDS that review-pr-acquire.sh, review-pr-checkout.sh, review-pr-diff.sh and review-pr-post.sh give each single-request gh read.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_LOOKUP_TIMEOUT_SECONDS=10

# `gh pr list --json reviews` embeds `reviews(first: 100)` in its GraphQL query, an implementation detail of gh verified in gh 2.101.0, so a list that long may be a prefix.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_GH_REVIEWS_LIMIT=100

# Display bounds for the stack document. No protocol or vendor limit backs either value.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_TEXT_ECHO_LIMIT=80
# The first distinct reviews in the order gh returns them are kept, and the rest are only counted.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT=10

# Ends a walk failure that is a property of the repository, not of the call.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE='Re-running will not help, and neither will review-pr-diff.sh, which runs the same walk.'

# Ends a walk failure that a gh call caused.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_STACK_GH_FAILURE_NOTE='One re-run may help a transient failure. A token that cannot read pull request reviews fails the same way every time, and review-pr-diff.sh runs the same walk.'

# The checkout and diff scripts' exit status when an ancestor PR has no submitted review.
# shellcheck disable=SC2034 # read by the scripts that source this file; export would leak it into every child process
REVIEW_PR_EXIT_STACK_UNREVIEWED=4

# The `gh pr list --json` fields one lookup reads.
_REVIEW_PR_STACK_LOOKUP_JSON_FIELDS='number,title,author,headRefName,baseRefName,isCrossRepository,reviews'

# jq program for review_pr_stack_lookup_verdict. Reads the lookup's stdout slurped, so it must be exactly one JSON array.
# Numbers above 2^53 are not exact integers in a jq double, so a PR number is bounded there.
# GitHub-sourced text in an entry is cut and escaped, and a review's association and state are limited to GitHub's documented values (GraphQL CommentAuthorAssociation and PullRequestReviewState) or the marker UNRECOGNIZED.
# shellcheck disable=SC2016 # the text is jq source holding `$branch` and similar bindings, and double quotes would expand them as shell variables.
_REVIEW_PR_STACK_LOOKUP_VERDICT_JQ_PROGRAM='
    def login_of: if type == "object" and (.login | type) == "string" and (.login | length) > 0 then .login else null end;
    def string_or_null: if type == "string" then . else null end;
    def escaped_text_or_null: if . == null then null else truncated_ascii_json_string($text_limit) end;
    def documented_value_or_marker($documented_values): if . == null then null elif . as $value | $documented_values | any(. == $value) then . else "UNRECOGNIZED" end;
    def documented_associations: ["COLLABORATOR", "CONTRIBUTOR", "FIRST_TIMER", "FIRST_TIME_CONTRIBUTOR", "MANNEQUIN", "MEMBER", "NONE", "OWNER"];
    def documented_review_states: ["PENDING", "COMMENTED", "APPROVED", "CHANGES_REQUESTED", "DISMISSED"];
    def has_control_character: explode | any(. < 32 or (. >= 127 and . <= 159));
    def well_formed_review:
      type == "object"
      and ((.state | type) == "string" and (.state | length) > 0)
      and (if (.state | ascii_upcase) == "APPROVED" or (.state | ascii_upcase) == "CHANGES_REQUESTED"
           then (.submittedAt | type) == "string" and (.submittedAt | length) > 0
           else true end);
    def well_formed_item($branch):
      type == "object"
      and ((.number | type) == "number" and .number >= 1 and .number <= 9007199254740991 and .number == (.number | floor))
      and ((.isCrossRepository | type) == "boolean")
      and .headRefName == $branch
      and ((.baseRefName | type) == "string" and (.baseRefName | length) > 0 and ((.baseRefName | has_control_character) | not))
      and ((.reviews | type) == "array" and (.reviews | all(well_formed_review)));
    def superseded($decisive_reviews):
      . as $review
      | $review.author != null and ($decisive_reviews | any(.author == $review.author and .submittedAt > $review.submittedAt));
    def ancestor_line:
      . as $pr
      | ($pr.author | login_of) as $pr_login
      # A gh reply without author.is_bot reads as a non-bot, so the own review of a bot-authored ancestor is not flagged self-review-only. Only the advice is affected, not the gate.
      | (if ($pr.author | type) == "object" and $pr.author.is_bot == true and $pr_login != null
         then $pr_login | ltrimstr("app/")
         else $pr_login end) as $comparable_pr_login
      | ($pr.reviews | map({
            author: (.author | login_of),
            association: (.authorAssociation | string_or_null),
            upper_state: (.state | ascii_upcase),
            submitted_at: (.submittedAt | string_or_null)
          } | .byPrAuthor = (.author != null and .author == $comparable_pr_login))) as $reviews
      | ($reviews | map(select(.upper_state != "PENDING"))) as $submitted_reviews
      | ($reviews | map(select(.upper_state == "APPROVED" or .upper_state == "CHANGES_REQUESTED")
          | {author, state: .upper_state, submittedAt: .submitted_at})) as $decisive_reviews
      | ($reviews | map({author, association: (.association | documented_value_or_marker(documented_associations)), state: (.upper_state | documented_value_or_marker(documented_review_states)), byPrAuthor})
          | reduce .[] as $review ([]; if any(.[]; . == $review) then . else . + [$review] end)) as $distinct_reviews
      | {
          number: $pr.number,
          title: ($pr.title | if type == "string" then . else "" end | truncated_ascii_json_string($text_limit)),
          author: ($pr_login | escaped_text_or_null),
          reviewed: (($submitted_reviews | length) > 0),
          changesRequested: ($decisive_reviews | any(.state == "CHANGES_REQUESTED" and (superseded($decisive_reviews) | not))),
          selfReviewOnly: ((($submitted_reviews | length) > 0) and ($submitted_reviews | all(.byPrAuthor))),
          reviewListFull: (($pr.reviews | length) >= $gh_reviews_limit),
          reviews: ($distinct_reviews[:$display_limit] | map(.author |= escaped_text_or_null)),
          reviewsNotShown: ([($distinct_reviews | length) - $display_limit, 0] | max)
        } as $entry
      | "ancestor\t\($pr.number)\t\($entry.reviewed)\t\($pr.baseRefName)\t\($entry | tojson)";
    if length != 1 then error("expected exactly one JSON value")
    else .[0]
      | if type != "array" then error("expected a JSON array")
        elif (all(well_formed_item($branch)) | not) then error("malformed item")
        elif length >= $lookup_limit then "truncated"
        else
          map(select(.isCrossRepository == false)) as $same_repository_items
          | if ($same_repository_items | length) == 0 then "none"
            elif ($same_repository_items | length) > 1 then "ambiguous\t" + ($same_repository_items | map(.number | tostring) | join(","))
            else $same_repository_items[0] | ancestor_line
            end
        end
    end
'

# jq program for review_pr_stack_document. Reads the ancestor entries slurped, in merge order.
# shellcheck disable=SC2016 # the text is jq source holding `$root_branch` and similar bindings, and double quotes would expand them as shell variables.
_REVIEW_PR_STACK_DOCUMENT_JQ_PROGRAM='
    def pr_labels: map("PR #\(.number)") | join(", ");
    . as $ancestors
    | ($root_branch == $default_branch and ($root_branch | length) > 0) as $root_is_default
    | ((($ancestors | length) > 0) or ($root_is_default | not)) as $stacked
    | (first(range(0; $ancestors | length) | select($ancestors[.].reviewed | not)) // null) as $lowest_index
    | [
        (if $root_is_default then empty else {
          code: "root-without-open-pr",
          message: "The root branch has no open pull request, so it may never have been reviewed. To review it, ask the owner of the root branch to open a PR from it into the default branch (a draft is enough), then review that PR. If you open it yourself, you become its author and your own review reads as self-review-only, and from then on that PR blocks every PR stacked on the branch until it has a review. If the branch has already merged, retarget the PR that targets it."
        } end),
        ($ancestors | map(select(.changesRequested)) | if length == 0 then empty else {
          code: "changes-requested",
          message: "Changes were requested and may still be open on: \(pr_labels). Proceed only once those changes are addressed and reviewed again."
        } end),
        ($ancestors | map(select(.selfReviewOnly)) | if length == 0 then empty else {
          code: "self-review-only",
          message: "Every submitted review is by the PR author on: \(pr_labels). Review each of those PRs directly by number, then return to this one."
        } end),
        ($ancestors | map(select(.reviewListFull)) | if length == 0 then empty else {
          code: "review-list-full",
          message: "Only the first \($reviews_limit) reviews were read on: \(pr_labels). Check the latest reviews on those PRs on GitHub before proceeding."
        } end)
      ] as $concerns
    | (if ($stacked | not) then {choices: [], recommended: null}
       elif $lowest_index != null then {
         choices: ["review-lowest", "stop"],
         recommended: (if ($root_is_default | not) or ($ancestors[:$lowest_index] | any(.changesRequested or .selfReviewOnly or .reviewListFull))
                       then "stop" else "review-lowest" end)
       }
       else {
         choices: ["proceed", "stop"],
         recommended: (if ($concerns | length) > 0 then "stop" else "proceed" end)
       } end) as $advice
    # Decision keys come first so a head preview of the output keeps them.
    | {
        stacked: $stacked,
        lowestUnreviewed: (if $lowest_index == null then null else $ancestors[$lowest_index].number end),
        recommendedChoice: $advice.recommended,
        choices: $advice.choices,
        concerns: $concerns,
        rootIsDefaultBranch: $root_is_default,
        rootBranch: ($root_branch | truncated_ascii_json_string($text_limit)),
        ancestors: $ancestors
      }
'

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

# Spells out each lowercase hex digit instead of using ranges, so a locale whose collation puts non-ASCII letters inside a range does not widen the class.
_REVIEW_PR_HEX_CHAR_REGEX='[0123456789abcdef]'

# review_pr_is_full_hex_oid VALUE
# Returns 0 only when VALUE is a full SHA-1 (40) or SHA-256 (64) lowercase hex object name.
# Anchored with no newline allowance, so a value with a trailing line, such as one carrying an option on the second line, fails.
review_pr_is_full_hex_oid() {
  [[ "$1" =~ ^${_REVIEW_PR_HEX_CHAR_REGEX}{40}(${_REVIEW_PR_HEX_CHAR_REGEX}{24})?$ ]]
}

# review_pr_base_is_default_branch BASE_REF_NAME DEFAULT_BRANCH
# Returns 0 only when both names are non-empty and byte-equal.
# Returns 1 when either is empty, so two absent names never read as the default branch. The comparison is case-sensitive, as git ref names are.
review_pr_base_is_default_branch() {
  [[ -n "$1" && "$1" == "$2" ]]
}

# Spells out each login character instead of using ranges, so a locale whose collation puts non-ASCII letters inside a range does not widen the class.
_REVIEW_PR_LOGIN_CHAR_REGEX='[ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_-]'

# The jq program that prints a /user response's `.login` only when the body is exactly one object whose `.login` is a string matching the `$login_pattern` argument.
# Slurping and requiring one document keeps `{"login":"oper"}{"login":"ator"}` from reading as one login.
# shellcheck disable=SC2016 # the text is jq source holding `$login_pattern`, and double quotes would expand it as a shell variable.
_REVIEW_PR_OPERATOR_LOGIN_JQ_FILTER='if length == 1 and (.[0] | type) == "object" and (.[0].login | type) == "string" and (.[0].login | test($login_pattern)) then .[0].login else empty end'

# The jq program that prints a `gh pr view --json author` response's `.author.login` only when the body is exactly one object whose `.author` is an object with a string `.login`.
# Any other shape raises an error, so an empty-string login stays distinguishable from an unreadable one.
_REVIEW_PR_AUTHOR_LOGIN_JQ_FILTER='if length == 1 and (.[0] | type) == "object" and (.[0].author | type) == "object" and (.[0].author.login | type) == "string" then .[0].author.login else error("unreadable author") end'

# review_pr_author_login PR_VIEW_JSON
# Prints the response's `.author.login` and returns 0 only when PR_VIEW_JSON is exactly one JSON object whose `.author` is an object with a string `.login`.
# Returns 1, printing nothing, for anything else, including empty input, so a failed `gh pr view` is an unreadable author.
# An empty-string login is readable: review_pr_authorship reads it as other.
review_pr_author_login() {
  printf '%s' "$1" | _lib_jq -s -j "$_REVIEW_PR_AUTHOR_LOGIN_JQ_FILTER" 2>/dev/null || return 1
}

# review_pr_authorship AUTHOR_LOGIN USER_JSON
# Prints self when AUTHOR_LOGIN equals USER_JSON's .login (gh api user's response) ignoring case, else other.
# Prints unknown when USER_JSON is not exactly one JSON object or its .login is not login-shaped; an empty or non-login-shaped AUTHOR_LOGIN is other.
# Login-shaped means entirely letters, digits, `_` and `-`, so a bot login carrying `/` is other.
# Always returns 0, so a caller under `set -e` reads the printed word.
review_pr_authorship() {
  local author_login="$1" user_json="$2" operator_login
  # The shape check runs inside jq so a login carrying the \u0000 escape is rejected rather than truncated.
  # A raw NUL byte never reaches here, because the callers' command substitution drops it.
  # \z, unlike $, does not tolerate a trailing newline.
  if ! operator_login=$(printf '%s' "$user_json" | _lib_jq -s -j --arg login_pattern "\\A${_REVIEW_PR_LOGIN_CHAR_REGEX}+\\z" "$_REVIEW_PR_OPERATOR_LOGIN_JQ_FILTER" 2>/dev/null) \
    || [[ -z "$operator_login" ]]; then
    printf 'unknown'
  # nocasematch folding is locale-dependent.
  # ASCII logins are accepted as folded.
  # A non-folding locale (unverified in tr_TR) reads other, which fails open.
  elif [[ "$author_login" =~ ^${_REVIEW_PR_LOGIN_CHAR_REGEX}+$ ]] && ! _lib_case_insensitive_ne "$author_login" "$operator_login"; then
    printf 'self'
  else
    printf 'other'
  fi
}

# review_pr_nul_names_to_json
# Reads NUL-terminated names on stdin and prints them as one compact JSON array, in order.
# Empty stdin prints [].
# Returns 1 when jq fails or the input does not end in NUL, so an unterminated final name is never dropped.
# Relies on a `jq -R -s` that keeps NUL bytes (README.md's `jq` version bullet names the verified version); a jq that truncates at NUL leaves the final name unterminated, so this returns 1.
review_pr_nul_names_to_json() {
  _lib_jq -R -s -c 'split("\u0000") | if length == 0 or .[-1] == "" then .[:-1] else error("unterminated final name") end' 2>/dev/null || return 1
}

# review_pr_bare_count RAW
# Prints the integer in RAW with surrounding whitespace removed, such as the left-padded count BSD `wc -c` prints.
# Returns 1, printing nothing, when RAW holds anything but one non-negative integer.
review_pr_bare_count() {
  [[ "$1" =~ ^[[:space:]]*([0-9]+)[[:space:]]*$ ]] || return 1
  printf '%s' "${BASH_REMATCH[1]}"
}

# review_pr_ascii_json_string VALUE [LIMIT]
# Prints VALUE as one ASCII-only JSON string, quotes included, escaped as review_pr_audit_match_lines escapes a path.
# With no LIMIT it makes no length cut. A LIMIT (a non-negative integer) cuts VALUE to that many characters first, then marks the cut `...[truncated]`.
# For an attacker-chosen name, such as a branch name, echoed in a message.
# When jq fails, prints a fixed placeholder instead and still returns 0, so a message built around it reaches its own exit.
review_pr_ascii_json_string() {
  # shellcheck disable=SC2016 # $value and $limit are jq bindings in a single-quoted filter, and double quotes would expand them as shell variables.
  _lib_jq -n -r --arg value "$1" --argjson limit "${2:-null}" "$REVIEW_PR_JQ_ASCII_JSON_STRING_DEFS"'$value | if $limit == null then ascii_json_string else truncated_ascii_json_string($limit) end' 2>/dev/null \
    || printf '"<value could not be escaped>"'
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
    --argjson path_limit "$REVIEW_PR_AUDIT_PATH_ECHO_LIMIT" \
    "$REVIEW_PR_JQ_ASCII_JSON_STRING_DEFS"'
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

# review_pr_git_status_description GIT_EXIT_STATUS CAP_SECONDS
# Prints "exited N", plus ", consistent with the CAP_SECONDSs cap firing" when N is a status a cap kill produces (see _lib_status_consistent_with_cap_kill).
review_pr_git_status_description() {
  if _lib_status_consistent_with_cap_kill "$1"; then
    printf 'exited %s, consistent with the %ss cap firing' "$1" "$2"
  else
    printf 'exited %s' "$1"
  fi
}

# review_pr_stack_lookup_verdict LOOKUP_STDOUT BRANCH
# Classifies one `gh pr list --head=BRANCH` reply (REVIEW_PR_STACK_LOOKUP_LIMIT items at most) and prints one line:
#   none                                                    — no open same-repository PR has BRANCH as its head
#   truncated                                               — the reply holds REVIEW_PR_STACK_LOOKUP_LIMIT items, so a PR past them would go unseen
#   ambiguous<TAB><number>,<number>,...                     — more than one open same-repository PR has BRANCH as its head
#   ancestor<TAB><number><TAB><reviewed><TAB><base><TAB><entry> — exactly one does; <reviewed> is true or false, <base> is its base branch, and <entry> is its one-line JSON stack-document entry
# A fork PR whose head branch has the same name is ignored (`--head` matches the name only).
# <reviewed> is true when any review's state, upper-cased, is not PENDING. Every other state counts, including DISMISSED, an empty-body review, a self-review and a state this code does not know.
# <entry> keeps only the allowlisted keys, so no review body, head ref or base ref reaches the document.
# <entry> carries the title and every login cut to REVIEW_PR_STACK_TEXT_ECHO_LIMIT characters and escaped as an ASCII-only JSON string, and an association or state outside GitHub's documented values as `UNRECOGNIZED`.
# Returns 1 with no output when the reply is not exactly one JSON array of well-formed items, or when jq fails.
review_pr_stack_lookup_verdict() {
  printf '%s' "${1-}" | _lib_jq -s -r \
    --arg branch "${2-}" \
    --argjson lookup_limit "$REVIEW_PR_STACK_LOOKUP_LIMIT" \
    --argjson gh_reviews_limit "$REVIEW_PR_STACK_GH_REVIEWS_LIMIT" \
    --argjson text_limit "$REVIEW_PR_STACK_TEXT_ECHO_LIMIT" \
    --argjson display_limit "$REVIEW_PR_STACK_REVIEW_DISPLAY_LIMIT" \
    "$REVIEW_PR_JQ_ASCII_JSON_STRING_DEFS$_REVIEW_PR_STACK_LOOKUP_VERDICT_JQ_PROGRAM" 2>/dev/null || return 1
}

# review_pr_stack_document ANCESTOR_LINES ROOT_BRANCH DEFAULT_BRANCH
# Prints the stack document as one line of JSON.
# ANCESTOR_LINES holds the entry of each ancestor PR, as review_pr_stack_lookup_verdict printed it, one per line in merge order: the PR nearest ROOT_BRANCH first and the target's own base PR last. It is empty for a target with no ancestor.
# ROOT_BRANCH is where the walk ended: DEFAULT_BRANCH, or a branch with no open PR.
# `stacked` is false only for an empty ANCESTOR_LINES and a ROOT_BRANCH equal to DEFAULT_BRANCH.
# Each concern's message names PRs only by their number.
# The entries arrive with their text already cut and escaped by review_pr_stack_lookup_verdict, so this function cuts and escapes only `rootBranch`, as an ASCII-only JSON string.
# Returns 1 with no output when jq fails, such as for a line that is not JSON.
review_pr_stack_document() {
  printf '%s' "${1-}" | _lib_jq -s -c \
    --arg root_branch "${2-}" \
    --arg default_branch "${3-}" \
    --argjson text_limit "$REVIEW_PR_STACK_TEXT_ECHO_LIMIT" \
    --argjson reviews_limit "$REVIEW_PR_STACK_GH_REVIEWS_LIMIT" \
    "$REVIEW_PR_JQ_ASCII_JSON_STRING_DEFS$_REVIEW_PR_STACK_DOCUMENT_JQ_PROGRAM" 2>/dev/null || return 1
}

# _review_pr_stack_walk_failure SCRIPT_NAME TARGET MESSAGE
# Prints one `SCRIPT_NAME: PR TARGET: MESSAGE` line on stderr, or `SCRIPT_NAME: MESSAGE` when TARGET is empty, and always returns 1, so `_review_pr_stack_walk_failure ... || return 1` ends the walk.
# TARGET is the `<owner>/<repo>#<number>` of the PR being walked, passed only once the walk has validated it.
_review_pr_stack_walk_failure() {
  if [[ -n "$2" ]]; then
    printf '%s: PR %s: %s\n' "$1" "$2" "$3" >&2
  else
    printf '%s: %s\n' "$1" "$3" >&2
  fi
  return 1
}

# _review_pr_stack_branch_echo BRANCH
# Prints BRANCH for a walk failure line: cut to REVIEW_PR_STACK_TEXT_ECHO_LIMIT characters and escaped as an ASCII-only JSON string.
_review_pr_stack_branch_echo() {
  review_pr_ascii_json_string "$1" "$REVIEW_PR_STACK_TEXT_ECHO_LIMIT"
}

# _review_pr_stack_walk_unreadable_reply SCRIPT_NAME TARGET BRANCH
# Prints the stderr line (see _review_pr_stack_walk_failure for TARGET) for a lookup reply of BRANCH that the walk could not read, and always returns 1.
# The line carries neither failure note, because a reply that is not a well-formed list is persistent when PR data caused it and transient when a jq cap kill did, and the walk cannot tell the two apart.
_review_pr_stack_walk_unreadable_reply() {
  _review_pr_stack_walk_failure "$1" "$2" "the stack walk could not read the reply to its lookup of open PRs with head branch $(_review_pr_stack_branch_echo "$3")."
}

# review_pr_walk_stack SCRIPT_NAME OWNER_REPO PR_NUMBER BASE_REF DEFAULT_BRANCH MODE
# Follows PR_NUMBER's base branch down through the open same-repository PRs that use each branch as their head, until a branch is the default branch or has no open PR. Makes one gh call per branch it looks up, at most REVIEW_PR_STACK_MAX_LOOKUPS.
# BASE_REF and DEFAULT_BRANCH are the PR's base branch and the repository's default branch, both non-empty. MODE is `document` or `lowest-unreviewed`.
# `document` prints the stack document (see review_pr_stack_document) and returns 0 only when it begins with `{`.
# `lowest-unreviewed` prints `clear`, or the number of the ancestor PR nearest the root that has no submitted review.
# Returns 1 with nothing on stdout and one `SCRIPT_NAME: ...` failure line on stderr when it cannot decide. A bad argument fails before any gh call.
# A failure that is a property of the repository ends in REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE and a gh failure in REVIEW_PR_STACK_GH_FAILURE_NOTE. A reply the walk could not read ends in neither. gh's own stderr is never echoed.
# A caller maps a return of 1 to its own exit status. Only the gate in the checkout and diff scripts exits REVIEW_PR_EXIT_STACK_UNREVIEWED.
# A branch name is echoed only as _review_pr_stack_branch_echo prints it, and a PR number only after a digits check.
# Every command whose failure ends the walk carries an explicit `|| return 1`, because callers run this inside `$(...)`, where errexit does not stop it.
review_pr_walk_stack() {
  local script_name="${1-}" owner_repo="${2-}" pr_number="${3-}" base_ref="${4-}" default_branch="${5-}" mode="${6-}"
  local target="" branch seen_pr_numbers lookups=0 ancestor_lines="" lowest_unreviewed="" deepest_pr=""
  local lookup_stdout lookup_status verdict verdict_rest candidate_numbers
  local ancestor_number ancestor_reviewed next_branch ancestor_entry cycle_numbers document

  case "$mode" in
    document|lowest-unreviewed) ;;
    *)
      _review_pr_stack_walk_failure "$script_name" "" "the stack walk was given an unknown mode $(review_pr_ascii_json_string "$mode")" || return 1 ;;
  esac
  if [[ -z "$base_ref" || -z "$default_branch" ]]; then
    _review_pr_stack_walk_failure "$script_name" "" "the stack walk needs the PR's base branch and the repository's default branch, and one is empty" || return 1
  fi
  if ! _lib_parse_pr_identity "$owner_repo#$pr_number" >/dev/null; then
    _review_pr_stack_walk_failure "$script_name" "" "the stack walk was given a repository or PR number it cannot use" || return 1
  fi
  # Leading zeros removed, so the cycle check compares the number the way gh prints it.
  pr_number=${pr_number#"${pr_number%%[!0]*}"}
  if [[ -z "$pr_number" ]]; then
    _review_pr_stack_walk_failure "$script_name" "" "the stack walk was given PR number zero" || return 1
  fi

  target="$owner_repo#$pr_number"
  branch="$base_ref"
  seen_pr_numbers=" $pr_number "
  while :; do
    if review_pr_base_is_default_branch "$branch" "$default_branch"; then
      break
    fi
    # `gh pr list --head=` with an empty value returns every open PR, so an empty name must never reach it.
    if [[ -z "$branch" ]]; then
      _review_pr_stack_walk_failure "$script_name" "$target" "an ancestor PR's base branch name is empty" || return 1
    fi
    if (( lookups >= REVIEW_PR_STACK_MAX_LOOKUPS )); then
      _review_pr_stack_walk_failure "$script_name" "$target" "the walk used all $REVIEW_PR_STACK_MAX_LOOKUPS of its lookups without confirming where the stack ends, and the deepest PR it reached is PR #$deepest_pr, whose base is branch $(_review_pr_stack_branch_echo "$branch"). This PR becomes reviewable through the skill only once the stack is shorter, for example after lower PRs merge. Reviewing PR #$deepest_pr directly by number does not by itself change that. $REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE" || return 1
    fi
    lookups=$((lookups + 1))

    lookup_status=0
    lookup_stdout=$(_lib_gh "$REVIEW_PR_STACK_LOOKUP_TIMEOUT_SECONDS" pr list -R "$owner_repo" "--head=$branch" --state open --limit "$REVIEW_PR_STACK_LOOKUP_LIMIT" --json "$_REVIEW_PR_STACK_LOOKUP_JSON_FIELDS" 2>/dev/null) || lookup_status=$?
    if (( lookup_status != 0 )); then
      _review_pr_stack_walk_failure "$script_name" "$target" "the stack walk's lookup of open PRs with head branch $(_review_pr_stack_branch_echo "$branch") $(review_pr_gh_status_description "$lookup_status"). $REVIEW_PR_STACK_GH_FAILURE_NOTE" || return 1
    fi

    verdict=$(review_pr_stack_lookup_verdict "$lookup_stdout" "$branch") || verdict=""
    case "$verdict" in
      none)
        break ;;
      truncated)
        _review_pr_stack_walk_failure "$script_name" "$target" "$REVIEW_PR_STACK_LOOKUP_LIMIT or more open PRs have head branch $(_review_pr_stack_branch_echo "$branch"), so the walk cannot tell which one is this PR's ancestor. No route through this skill exists while that many open PRs share the branch name. $REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE" || return 1 ;;
      ambiguous$'\t'*)
        candidate_numbers=${verdict#ambiguous$'\t'}
        if [[ ! "$candidate_numbers" =~ ^[0-9]+(,[0-9]+)+$ ]]; then
          _review_pr_stack_walk_unreadable_reply "$script_name" "$target" "$branch" || return 1
        fi
        _review_pr_stack_walk_failure "$script_name" "$target" "more than one open PR has head branch $(_review_pr_stack_branch_echo "$branch"): PR numbers ${candidate_numbers//,/, }. This PR stays unreviewable through the skill until all but one of them are closed or retargeted. $REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE" || return 1 ;;
      ancestor$'\t'*)
        verdict_rest=${verdict#ancestor$'\t'}
        ancestor_number=${verdict_rest%%$'\t'*}
        verdict_rest=${verdict_rest#*$'\t'}
        ancestor_reviewed=${verdict_rest%%$'\t'*}
        verdict_rest=${verdict_rest#*$'\t'}
        next_branch=${verdict_rest%%$'\t'*}
        ancestor_entry=${verdict_rest#*$'\t'}
        if [[ ! "$ancestor_number" =~ ^[1-9][0-9]*$ ]] || [[ "$ancestor_reviewed" != "true" && "$ancestor_reviewed" != "false" ]] || [[ "$ancestor_entry" != "{"* ]]; then
          _review_pr_stack_walk_unreadable_reply "$script_name" "$target" "$branch" || return 1
        fi ;;
      *)
        _review_pr_stack_walk_unreadable_reply "$script_name" "$target" "$branch" || return 1 ;;
    esac

    case "$seen_pr_numbers" in
      *" $ancestor_number "*)
        # The cycle is the chain from the first sighting of this number onward.
        cycle_numbers="$ancestor_number ${seen_pr_numbers#*" $ancestor_number "}"
        cycle_numbers=${cycle_numbers% }
        _review_pr_stack_walk_failure "$script_name" "$target" "the base branches form a cycle through PR numbers ${cycle_numbers// /, }. One of those PRs must be retargeted. $REVIEW_PR_STACK_PERSISTENT_CAUSE_NOTE" || return 1 ;;
    esac
    seen_pr_numbers="$seen_pr_numbers$ancestor_number "
    deepest_pr="$ancestor_number"
    # The walk runs from the target down, so a later ancestor is nearer the root and the last unreviewed one is the lowest.
    if [[ "$ancestor_reviewed" == "false" ]]; then
      lowest_unreviewed="$ancestor_number"
    fi
    ancestor_lines="$ancestor_entry"$'\n'"$ancestor_lines"
    branch="$next_branch"
  done

  if [[ "$mode" == "lowest-unreviewed" ]]; then
    printf '%s' "${lowest_unreviewed:-clear}"
    return 0
  fi
  document=$(review_pr_stack_document "$ancestor_lines" "$branch" "$default_branch") || document=""
  if [[ "$document" != "{"* ]]; then
    _review_pr_stack_walk_failure "$script_name" "$target" "the stack walk could not assemble the stack document" || return 1
  fi
  printf '%s' "$document"
}

# review_pr_checks_token_source PR_URL OVERRIDE_TOKEN
# Prints `ambient` when OVERRIDE_TOKEN is empty, `override` when it is non-empty and PR_URL begins with `https://github.com/` (host case-insensitive), else `withheld`.
# PR_URL is the `url` field of `gh pr view --json`, which names the host the PR lives on; an unset GH_HOST does not.
# The caller pins GH_HOST=github.com on the `override` call, so the token only ever goes to github.com.
# The match is anchored at the start and requires the `/` after the host, so a port, userinfo, a subdomain, a suffix domain, or a bare host does not match.
# Prints only the verdict, so the token never reaches a caller's output.
review_pr_checks_token_source() {
  local pr_url="${1-}" override_token="${2-}"
  if [[ -z "$override_token" ]]; then
    printf 'ambient'
    return 0
  fi
  case "$pr_url" in
    https://[Gg][Ii][Tt][Hh][Uu][Bb].[Cc][Oo][Mm]/*) printf 'override' ;;
    *) printf 'withheld' ;;
  esac
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
