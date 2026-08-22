#!/bin/bash
# hook-class: gate
# Gate: require /respond-pr when fetching or posting PR comments.
#
# Why: Claude habitually fetches only inline file comments
# (gh api .../pulls/N/comments) and misses top-level reviews and issue-level
# comments, which is the common PR response failure mode. The /respond-pr
# skill fetches all three AND enforces the [Claude Code] attribution prefix
# on replies.
#
# Bypass: the /respond-pr skill writes a marker at
# ~/.claude/.respond-pr-active.d/<session_id> at its start and removes it at
# the end. While THIS session's marker exists AND its stored PID is alive
# (kill -0), this hook lets gh commands through so the skill itself doesn't
# recurse into its own gate. Per-session keying (vs. a singleton path)
# prevents two parallel respond-pr sessions from thrashing on cleanup, and
# prevents one session's marker from leaking bypass to unrelated parallel
# sessions — both of which the singleton design did not handle.
#
# Eviction (dead PID, or a live PID whose mtime has idled past 60 minutes)
# and the touch-on-use refresh that keeps a live marker from expiring
# mid-run are documented in docs/hooks.md's "Gate deadlock recovery" section.
# The gate also covers `repos/{o}/{r}/(pulls|issues)/comments/{id}` (no
# PR/issue-number segment) — the destructive PATCH endpoint that overwrites
# a comment in place; gating it forces any edit to flow through
# /respond-pr's verified-author guidance — the GraphQL comment mutations,
# which post exactly as `gh pr comment` does; and `gh issue comment`, which
# posts through POST /repos/{o}/{r}/issues/{n}/comments, the same endpoint
# `gh pr comment` reaches, and whose --edit-last/--delete-last forms rewrite
# and remove already-posted bodies.
#
# Fail posture: closed. Unparseable input denies (via _lib.sh), and every
# match is written loose on the principle that a false deny costs one
# /respond-pr redirect while a false allow posts an unattributed comment.
#
# Threat model: cooperative, not adversarial. This gate targets the command
# shapes the model writes unprompted, which is where the missing-attribution
# failure actually happens. It does not try to defeat deliberate evasion: a
# quoted subcommand (gh pr 'comment'), variable indirection
# (SUB=comment; gh pr $SUB), a `gh alias` shorthand, and piping into xargs
# all reach through, and regex over raw command text cannot close that class
# by construction. Arms for those shapes would buy nothing against an intent
# that could as easily drive the REST API from a script. Read a deny here as
# "this looks like the habitual mistake," not as a security boundary.
#
# Known gaps: a GraphQL *read* whose query text happens to contain a gated
# REST path shape is denied; a multi-line call is matched as one string, so
# an unrelated neighboring command can be denied alongside a gated one; and
# the gate cannot see inside a query body sourced from a file, so it denies
# those wholesale rather than inspecting them.
#
# Second bypass path: /review-pr's active marker at
# ~/.claude/.review-pr-active.d/<session_id> releases a matched READ the
# same way respond-pr's does. A matched WRITE (posting a `gh pr review`) is
# never released by the active marker alone -- it additionally requires
# /review-pr's completion marker (~/.claude/review-pr-markers/<repo-hash>.
# <session_id>) to name this exact HEAD, PR number, and (when the command
# posts a body file) body hash, proving the review happened rather than
# merely that a post was authorized. See _lib_review_pr_completion_marker_fields
# in _lib.sh for the session-scoped read and marker.sh's `write review-pr`
# arm for what writes it. Named accepted gap: an inline (non-file) --body
# value has no file to hash, so the body-hash check does not apply to it.
#
# `gh pr edit` with a body-mutating flag (--body/--body-file) is also
# gated here, independent of either marker: the "never edit someone else's
# PR body" invariant otherwise rests on skill prose alone.

set -uo pipefail

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf '%s\n' "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "Blocked by respond-pr gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked by respond-pr gate: could not parse tool-input JSON."

# Only gate Bash tool use
if [ "$TOOL_NAME" != "Bash" ]; then
  exit 0
fi

# Bypass: live marker for THIS session's session_id means we're inside the
# skill and should let its own gh commands through. An empty session_id (older
# Claude Code versions, payload-schema drift) or a path-escaping one falls
# through to the gate.
SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty')
if _lib_active_bypass_marker_live_and_touch ".respond-pr-active.d" "$SESSION_ID"; then
  exit 0
fi

# review-pr active marker: computed here, used further down. Unlike
# respond-pr's blanket bypass above, this does NOT exit 0 unconditionally --
# a live marker only proves a /review-pr session is running, never that the
# review itself happened. It releases reads unconditionally further below;
# a write additionally requires the completion marker checked there.
REVIEW_PR_ACTIVE=0
if _lib_active_bypass_marker_live ".review-pr-active.d" "$SESSION_ID"; then
  REVIEW_PR_ACTIVE=1
fi

# grep matches within a line and `.` never crosses a newline, so any command
# written across multiple lines slips every arm below. Flatten first: this
# gate reasons about the whole command, not line by line. GraphQL bodies are
# routinely multi-line, but the same hole applied to a wrapped REST URL — the
# fix belongs to all four arms, not just the GraphQL one.
#
# Flattening also fuses genuinely separate commands, so a read on one line and
# an unrelated `grep addCommentHandler` on the next are matched as one string
# and denied together. That is a false deny, costing a needless /respond-pr
# redirect — the same direction the loose matching below already accepts, and
# the opposite of the false allow that would leak an unattributed comment.
# Two steps, because the two kinds of newline join differently and collapsing
# them alike reopens the hole this closes. A backslash-newline is a shell line
# continuation: the shell removes both characters and joins with nothing, so
# `pulls/1/\<newline>comments` executes as `pulls/1/comments`. Substituting a
# space there would split the path and every URL arm would miss it — the gate
# would be reading a command the shell never runs. A bare newline separates
# commands, and joins with a space.
#
# GH-801: awk with RS = "\0" (matching _mask_shell_quotes's identical
# technique in deny-invisible-commit-content.sh), not a per-line sed —
# a per-line tool never sees an embedded newline character to substitute in
# the first place, since the newline itself is what separates its input
# into lines; slurping the whole command as one record is what lets a
# single gsub reach across it. Checked and fail-closed: awk missing,
# killed, or erroring denies explicitly below rather than falling through
# to this gate's normal "no arm matched, allow" path — matching
# deny-invisible-commit-content.sh's own COMMAND_UNQUOTED precedent.
COMMAND_FLAT=$(printf '%s' "$COMMAND" | awk 'BEGIN { RS = "\0" } { gsub(/\\\n/, ""); gsub(/\n/, " "); printf "%s", $0 }')
COMMAND_FLAT_EXIT=$?
if [ "$COMMAND_FLAT_EXIT" -ne 0 ]; then
  emit_deny "Blocked by respond-pr gate: could not flatten the command text (exit ${COMMAND_FLAT_EXIT}) — awk may be missing, killed, or errored. Failing closed rather than evaluating an unflattened command that could hide a gated pattern across a line break."
  exit 0
fi

# Match PR comment read/write patterns. Forms:
#   gh api .../pulls/N/comments       (inline review comments)
#   gh api .../pulls/N/reviews        (top-level review bodies)
#   gh api .../issues/N/comments      (issue-level, which GH uses for PR top-level threads)
#   gh pr comment ...                 (post a top-level comment)
#   gh pr review ...                  (post a review)
#   gh issue comment ...              (posts to the same issues/N/comments endpoint)
#   gh api graphql ... addComment ... (and the other comment mutations)
#
# The GraphQL arms gate writes only, never a query. The REST arms gate reads
# too, to force a complete fetch through /respond-pr; that rationale does not
# extend here, because a single GraphQL query already returns all three
# comment kinds in one round trip and so cannot produce the partial fetch the
# REST gating exists to prevent. What must not slip through is the write:
# these mutations post comments exactly as `gh pr comment` does, and posting
# outside /respond-pr skips the [Claude Code] attribution prefix that
# discloses AI authorship to outside readers of a public PR.
#
# Match the mutation name, not the string "mutation": GraphQL's operation
# keyword is optional in shorthand, so a body can mutate without it. The
# name is a write verb, any middle segment, then the object — the middle is
# what makes updateIssueComment and addDiscussionComment match, since their
# verb and object are not adjacent. `submit` earns its place next to
# add/update/delete because submitPullRequestReview publishes a pending
# review body; it is the GraphQL twin of the `gh pr review` arm 3 gates.
# Deliberately loose: an unforeseen mutation matching too eagerly costs a
# false deny, which /respond-pr absorbs, while one missed posts an
# unattributed comment. Verbs outside this set (minimizeComment,
# resolveReviewThread, addReaction) alter no comment body and stay allowed.
# A query body read from a file cannot be inspected, so it cannot be cleared.
# `-f/-F query=@file` and `--input file` are ordinary documented `gh api`
# usage, not exotic evasions, and either would carry an unreviewable mutation
# straight past the mutation-name match. Deny and let /respond-pr handle it:
# this also denies a file-sourced *read*, which is the accepted cost of not
# being able to tell the two apart.
# Each pattern is named once and shared by the arm chain and the write-signal
# list below, which put different questions to the same command: "is this a
# gated surface at all?" and "is this a write?". Naming them once keeps the two
# from drifting apart, which is the failure that lets a command match an arm
# and then be misjudged a read.
#
# POSIX [[:space:]] throughout, never GNU's \s: a class BSD grep does not
# honour makes an arm silently miss, and a missed arm falls through to allow —
# a fail-open in a gate whose stated posture is closed.
#
# Bash [[ =~ ]] rather than `printf | grep`: this chain runs on every Bash tool
# call, and each arm was two forks. The extractions further down keep `sed`;
# they run only after an arm has already matched, so they are not on the hot
# path, and `sed`'s greedy last-match semantics differ from the leftmost match
# [[ =~ ]] would give.
# The `[^|&;]*` between `api` and the target (a REST path below, `graphql`
# here) tolerates a flag interposed before the target, e.g.
# `gh api -H "Accept: ..." graphql ...`: a documented, valid `gh api` call
# whose target is a *positional* argument, not necessarily the first token
# after `api`. Requiring the two literally adjacent would miss that shape
# and fall through to allow, since no other arm matches a bare `graphql`
# positional.
#
# `gh pr` and `gh issue` need the same tolerance for the same reason, but not
# the same expression. `gh` accepts `-R`/`--repo` anywhere ahead of the verb —
# `gh --repo o/r pr comment N`, `gh pr --repo o/r comment N`, and
# `gh pr comment N --repo o/r` all dispatch the same write — so requiring the
# verb adjacent to `pr`/`issue` misses the first two, and a missed arm allows.
# Hence the run appears twice below, once in each position the flag can take.
# All three spellings of its value are accepted: separated, `=`-joined, and
# glued short-form (`-Ro/r`).
#
# The tolerated span is that one flag and its value, not arbitrary text.
# `[^|&;]*` is safe after `api`, whose target is a distinctive URL or the
# literal `graphql`; it is not safe here, because `comment` and `review` are
# ordinary words that appear in search strings and titles, so an unbounded
# span would deny `gh pr list --search comment`. The value is required rather
# than optional for the same reason in miniature: with it optional, the regex
# engine may decline to consume the flag's value and match the value itself
# as the verb, so `gh pr -R review view 5` — a read — would deny. The
# separator stays optional only because a glued value has none; a space after
# the flag still forces the following token to be read as the value.
PATTERN_REPO_FLAG_RUN='((-R|--repo)([[:space:]]+|=)?[^[:space:]]+[[:space:]]+)*'
PATTERN_REST_NUMBERED='gh[[:space:]]+api[[:space:]]+[^|&;]*(pulls|issues)/[0-9]+/(comments|reviews)'
PATTERN_REST_COMMENT_ID='gh[[:space:]]+api[[:space:]]+[^|&;]*repos/[^/[:space:]]+/[^/[:space:]]+/(pulls|issues)/comments/[0-9]+'
PATTERN_PR_WRITE_CMD='gh[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'pr[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'(comment|review)([[:space:]]|$)'
PATTERN_ISSUE_WRITE_CMD='gh[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'issue[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'comment([[:space:]]|$)'
PATTERN_GRAPHQL_MUTATION='gh[[:space:]]+api[[:space:]]+[^|&;]*graphql[^|&;]*(add|update|delete|submit)[A-Za-z]*(Comment|Review)'
PATTERN_GRAPHQL_FILE_BODY='gh[[:space:]]+api[[:space:]]+[^|&;]*graphql[^|&;]*(query=@|--input([[:space:]]|=))'
PATTERN_ANY_FILE_BODY='gh[[:space:]]+api[[:space:]]+[^|&;]*(query=@|--input([[:space:]]|=))'
PATTERN_FIELD_FLAG='(-f|-F|--field|--raw-field)[[:space:]=]'
PATTERN_MUTATING_METHOD='(-X|--method)[[:space:]=]*(POST|PATCH|PUT|DELETE)'
# `gh pr edit` covers title, labels, reviewers, and more -- only the
# body-mutating forms fold into this gate ("never edit someone else's PR
# body" is the invariant being closed here, not every `pr edit` use). Two
# separate patterns rather than one combined regex: bash ERE has no
# lookahead, so "pr edit ... AND a body flag somewhere in the command" is
# expressed as two `[[ ]]` tests joined by `&&`, not one alternation.
PATTERN_PR_EDIT_CMD='gh[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'pr[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'edit([[:space:]]|$)'
PATTERN_PR_EDIT_BODY_FLAG='(--body|--body-file)([[:space:]]|=)'
# Used only by the review-pr write-authorization block below: an approval is
# never released there regardless of the rest of that block's checks, since
# /review-pr's own design never emits one autonomously (see SKILL.md).
#
# The CLI form combines an allowlist with a denylist -- neither alone is
# sufficient. `gh pr review --help` documents -a/--approve, -c/--comment, and
# -r/--request-changes as the only verdict flags. The allowlist
# (PATTERN_REVIEW_PR_VERDICT_FLAG) requires one of the two non-approving
# flags be present, closing every --approve spelling, known or not, rather
# than adding another spelling to chase -- but presence of a non-approving
# flag does not imply absence of an approving one: `gh pr review N --approve
# --comment -F body` carries both, and gh itself is not relied on to reject
# that combination. The denylist (PATTERN_REVIEW_PR_APPROVE_FLAG_CLI) closes
# that gap by matching known --approve spellings (short, long, and
# `=`-joined) unconditionally, regardless of what else the command carries.
PATTERN_PR_REVIEW_CMD='gh[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'pr[[:space:]]+'"$PATTERN_REPO_FLAG_RUN"'review([[:space:]]|$)'
# Trailing boundary includes `=`: pflag-based boolean flags accept an
# `=`-joined explicit value (`--comment=true`), and without it here that
# gh-valid spelling would be wrongly denied by this allowlist.
PATTERN_REVIEW_PR_VERDICT_FLAG='(^|[[:space:]])(-c|--comment|-r|--request-changes)([[:space:]]|=|$)'
PATTERN_REVIEW_PR_APPROVE_FLAG_CLI='(^|[[:space:]])(-a|--approve)([[:space:]]|=|$)'
# The REST form's `event` field is a small fixed vocabulary
# (COMMENT|REQUEST_CHANGES|APPROVE) with no shorthand spelling, so denylisting
# APPROVE here carries none of the CLI flag's bypass risk (GitHub's
# review-event values are case-sensitive uppercase, so no case-folding is
# needed -- any other casing already fails at the API rather than reaching
# this hook as a real approval).
PATTERN_REVIEW_PR_API_EVENT_APPROVE='(-f|--field|--raw-field)[[:space:]=]+event=APPROVE([[:space:]]|$)'

if [[ "$COMMAND_FLAT" =~ $PATTERN_REST_NUMBERED ]]; then
  :
elif [[ "$COMMAND_FLAT" =~ $PATTERN_REST_COMMENT_ID ]]; then
  :
elif [[ "$COMMAND_FLAT" =~ $PATTERN_PR_WRITE_CMD ]]; then
  :
elif [[ "$COMMAND_FLAT" =~ $PATTERN_ISSUE_WRITE_CMD ]]; then
  :
elif [[ "$COMMAND_FLAT" =~ $PATTERN_GRAPHQL_MUTATION ]]; then
  :
elif [[ "$COMMAND_FLAT" =~ $PATTERN_GRAPHQL_FILE_BODY ]]; then
  :
elif [[ "$COMMAND_FLAT" =~ $PATTERN_PR_EDIT_CMD ]] && [[ "$COMMAND_FLAT" =~ $PATTERN_PR_EDIT_BODY_FLAG ]]; then
  :
else
  exit 0
fi

# Is any write present? Asked independently of which arm above matched, because
# that chain stops at the first hit: a call that reads one endpoint and writes
# another settles on the read arm and would otherwise be judged a read. The
# REST endpoints serve both verbs, so the URL alone does not say which this is.
# One signal per entry, matching the arm chain above, so each keeps its reason:
# the comment-posting commands; a field flag, because `gh api` issues POST
# whenever one is present and so the absence of -X does not imply a read; and a
# request body sourced from a file, which is a body whatever the endpoint — the
# file form belongs to the REST endpoints as much as to graphql, and scoping it
# to graphql alone left the REST arms able to post a comment that read as a
# fieldless GET.
GATED_WRITE=0
gated_write_patterns=(
  "$PATTERN_PR_WRITE_CMD"
  "$PATTERN_ISSUE_WRITE_CMD"
  "$PATTERN_GRAPHQL_MUTATION"
  "$PATTERN_FIELD_FLAG"
  "$PATTERN_ANY_FILE_BODY"
)
for write_signal in "${gated_write_patterns[@]}"; do
  if [[ "$COMMAND_FLAT" =~ $write_signal ]]; then
    GATED_WRITE=1
    break
  fi
done

# `gh pr edit` carries no read form -- reaching the arm chain above (which
# requires the body flag too) already means this is a write, so it is set
# directly rather than added to gated_write_patterns above, which would
# make the body flag alone (with no `pr edit` anywhere) count as a write
# signal for every OTHER matched arm too.
if [[ "$COMMAND_FLAT" =~ $PATTERN_PR_EDIT_CMD ]] && [[ "$COMMAND_FLAT" =~ $PATTERN_PR_EDIT_BODY_FLAG ]]; then
  GATED_WRITE=1
fi

# The mutating-method signal is checked separately because it is the one that
# must fold case, and the patterns above must not: `repos/` path segments and
# GraphQL mutation names are case-significant. `gh` normalizes the method
# before sending, so `-X delete` issues a real DELETE — matching only the
# uppercase spelling let the lowercase form through as though it were a read.
# Folding also lets `-X` match a bare `-x`, which is not a gh flag; that can
# only ever add a false deny, which this gate already accepts by design.
shopt -s nocasematch
if [[ "$COMMAND_FLAT" =~ $PATTERN_MUTATING_METHOD ]]; then
  GATED_WRITE=1
fi
shopt -u nocasematch

# review-pr read bypass: an active marker releases a matched READ
# unconditionally (step 1 needs the complete three-endpoint fetch the same
# way an author does). A matched WRITE is never released here -- that needs
# the completion-marker check below, which an active marker alone cannot
# stand in for.
if [ "$REVIEW_PR_ACTIVE" -eq 1 ] && [ "$GATED_WRITE" -eq 0 ]; then
  exit 0
fi

# Repo the gated command targets: the explicit -R/--repo flag or a
# repos/OWNER/REPO/... path. The write-authorization block below needs this
# ahead of its own exit paths, to bind a posted review to the repo
# /review-pr actually reviewed, not only its PR number and HEAD -- so it is
# extracted here rather than reusing the cross-repo bypass's own extraction
# further down, which runs too late for this block to read. Two explicit
# forms are recognized:
#   gh api repos/OWNER/REPO/...
#   gh pr <cmd> ... -R OWNER/REPO      (also --repo OWNER/REPO, --repo=OWNER/REPO)
# Reads COMMAND_FLAT, not COMMAND: `sed` is per-line exactly as grep is, so a
# wrapped cross-repo URL would otherwise be invisible here while the arm
# chain above already saw it — the two would disagree about the same
# command. That direction fails closed (the repo goes unrecognized and the
# command denies), but a gate whose two halves read different text is a gate
# whose behaviour cannot be reasoned about from either half alone.
COMMAND_REPO=$(printf '%s\n' "$COMMAND_FLAT" | sed -nE 's,.*repos/([^/]+/[^/]+)/(pulls|issues)/[0-9]+/(comments|reviews).*,\1,p;s,.*repos/([^/]+/[^/]+)/(pulls|issues)/comments/[0-9]+.*,\1,p' | head -1)
if [ -z "$COMMAND_REPO" ]; then
  COMMAND_REPO=$(printf '%s\n' "$COMMAND_FLAT" | sed -nE 's,.*[[:space:]](-R|--repo)[[:space:]=]+([^[:space:]=]+/[^[:space:]]+).*,\2,p' | head -1)
fi

# `gh api repos/{owner}/{repo}/...` is documented gh syntax: gh substitutes
# the current repo at call time. The placeholder is literal text, so it reads
# as a repo name unlike any origin and would otherwise release the very
# same-repo access this gate exists to catch.
if [[ "$COMMAND_REPO" == *[{}]* ]]; then
  COMMAND_REPO=""
fi

# review-pr write authorization. Requires, all checked against THIS
# session's own completion marker (never a cross-session glob -- see
# _lib_review_pr_completion_marker_fields in _lib.sh): the marker's stored
# headRefOid equals the worktree's current HEAD; the marker's stored PR
# identity equals the PR number the command being run actually targets; the
# repo the command targets (COMMAND_REPO above, or -- when the command
# carries neither an explicit flag nor a repos/OWNER/REPO/ path -- this
# worktree's own origin remote, resolved the same way gh itself would)
# equals the marker's stored owner/repo; and, when the command posts a body
# via -F/--body-file, that file's hash equals the marker's stored body hash.
# Any missing piece (no completion marker, unresolvable worktree root,
# unparseable PR number, unresolvable target repo) leaves
# REVIEW_PR_WRITE_AUTHORIZED at 0 and falls through to the existing deny
# below -- fail-closed by construction, not by an explicit check.
REVIEW_PR_WRITE_AUTHORIZED=0
if [ "$REVIEW_PR_ACTIVE" -eq 1 ] && [ "$GATED_WRITE" -eq 1 ]; then
  if REVIEW_PR_CONFIG_DIR=$(_lib_config_dir 2>/dev/null); then
    REVIEW_PR_REPO_ROOT=$(_lib_capped git rev-parse --show-toplevel 2>/dev/null)
    if [ -n "$REVIEW_PR_REPO_ROOT" ]; then
      REVIEW_PR_REPO_HASH=$(_marker_lib_repo_hash "$REVIEW_PR_REPO_ROOT")
      if REVIEW_PR_MARKER_FIELDS=$(_lib_review_pr_completion_marker_fields "$REVIEW_PR_CONFIG_DIR" "$REVIEW_PR_REPO_HASH" "$SESSION_ID"); then
        REVIEW_PR_MARKER_PR_IDENTITY=$(printf '%s\n' "$REVIEW_PR_MARKER_FIELDS" | sed -n '1p')
        REVIEW_PR_MARKER_HEAD_REF_OID=$(printf '%s\n' "$REVIEW_PR_MARKER_FIELDS" | sed -n '2p')
        REVIEW_PR_MARKER_BODY_HASH=$(printf '%s\n' "$REVIEW_PR_MARKER_FIELDS" | sed -n '3p')
        REVIEW_PR_MARKER_PR_NUMBER="${REVIEW_PR_MARKER_PR_IDENTITY##*#}"
        REVIEW_PR_MARKER_OWNER_REPO="${REVIEW_PR_MARKER_PR_IDENTITY%#*}"

        REVIEW_PR_CURRENT_HEAD=$(_lib_capped git rev-parse HEAD 2>/dev/null)

        # PR number: the integer immediately following the `review` verb
        # (CLI form) or the path segment between `pulls/` and the next `/`
        # (API form) -- the same two shapes PATTERN_PR_WRITE_CMD and
        # PATTERN_REST_NUMBERED above already gate on. More than one
        # distinct number across both extractions is an unresolvable
        # ambiguity (e.g. two chained gh calls); GATED_COMMAND_PR_NUMBER
        # stays empty rather than guessing, which never matches below.
        REVIEW_PR_PR_NUMBER_CLI_MATCHES=$(printf '%s\n' "$COMMAND_FLAT" | grep -oE '[[:space:]]review[[:space:]]+[0-9]+([[:space:]]|$)' | grep -oE '[0-9]+')
        REVIEW_PR_PR_NUMBER_API_MATCHES=$(printf '%s\n' "$COMMAND_FLAT" | grep -oE 'pulls/[0-9]+/' | grep -oE '[0-9]+')
        REVIEW_PR_PR_NUMBER_CANDIDATES=$(printf '%s\n%s\n' "$REVIEW_PR_PR_NUMBER_CLI_MATCHES" "$REVIEW_PR_PR_NUMBER_API_MATCHES" | grep -v '^$' | sort -u)
        GATED_COMMAND_PR_NUMBER=""
        if [ "$(printf '%s\n' "$REVIEW_PR_PR_NUMBER_CANDIDATES" | grep -c '.')" -eq 1 ]; then
          GATED_COMMAND_PR_NUMBER="$REVIEW_PR_PR_NUMBER_CANDIDATES"
        fi

        # Repo the command targets: COMMAND_REPO when the command carries an
        # explicit -R/--repo flag or repos/OWNER/REPO/ path, otherwise the
        # implicit current-directory-remote resolution gh itself would use.
        # A wrong resolution can never leak an unrelated repo's identity
        # into the comparison below -- it just fails to equal
        # REVIEW_PR_MARKER_OWNER_REPO, which denies.
        REVIEW_PR_TARGET_REPO="$COMMAND_REPO"
        if [ -z "$REVIEW_PR_TARGET_REPO" ]; then
          # _lib_capped, not bare git: a stale index lock or a
          # network-mounted .git would otherwise block this call for as
          # long as the filesystem takes to answer.
          REVIEW_PR_CURRENT_URL=$(_lib_capped git config --get remote.origin.url 2>/dev/null)
          if [ -n "$REVIEW_PR_CURRENT_URL" ]; then
            REVIEW_PR_TARGET_REPO=$(printf '%s\n' "$REVIEW_PR_CURRENT_URL" | sed -nE 's,.*[:/]([^/:]+/[^/]+)$,\1,p' | sed 's,\.git$,,')
          fi
        fi

        # --body-file / -F value, or gh api's -f/--field/--raw-field
        # key=@file form scoped to the `body` field: same quoting/=-joined/
        # space-separated tolerance as the -R/--repo extraction above
        # (COMMAND_REPO). Empty means no file-based body in this command --
        # the named accepted gap for an inline `--body "<text>"` form, which
        # has no file to hash, so the body-hash check does not apply to it.
        REVIEW_PR_BODY_FILE_PATH=$(printf '%s\n' "$COMMAND_FLAT" | sed -nE 's,.*[[:space:]](-F|--body-file)[[:space:]=]+([^[:space:]]+).*,\2,p;s,.*[[:space:]](-f|--field|--raw-field)[[:space:]=]+body=@([^[:space:]]+).*,\2,p' | head -1)
        REVIEW_PR_BODY_HASH_OK=1
        if [ -n "$REVIEW_PR_BODY_FILE_PATH" ]; then
          REVIEW_PR_BODY_HASH_OK=0
          REVIEW_PR_ACTUAL_BODY_HASH=$(_lib_capped sha256sum -- "$REVIEW_PR_BODY_FILE_PATH" 2>/dev/null | awk '{print $1}')
          if [ -n "$REVIEW_PR_ACTUAL_BODY_HASH" ] && [ "$REVIEW_PR_ACTUAL_BODY_HASH" = "$REVIEW_PR_MARKER_BODY_HASH" ]; then
            REVIEW_PR_BODY_HASH_OK=1
          fi
        fi

        if [ -n "$REVIEW_PR_CURRENT_HEAD" ] && [ "$REVIEW_PR_CURRENT_HEAD" = "$REVIEW_PR_MARKER_HEAD_REF_OID" ] \
          && [ -n "$GATED_COMMAND_PR_NUMBER" ] && [ "$GATED_COMMAND_PR_NUMBER" = "$REVIEW_PR_MARKER_PR_NUMBER" ] \
          && [ -n "$REVIEW_PR_TARGET_REPO" ] && [ "$REVIEW_PR_TARGET_REPO" = "$REVIEW_PR_MARKER_OWNER_REPO" ] \
          && [ "$REVIEW_PR_BODY_HASH_OK" -eq 1 ]; then
          REVIEW_PR_WRITE_AUTHORIZED=1
        fi
      fi
    fi
  fi
fi

# An approving verdict is never authorized here, independent of every check
# above: the skill's own design never emits one autonomously (it stays the
# human's separate action in the GitHub UI), so this hook backs that
# invariant regardless of whether the rest of this block would have allowed.
# CLI form: a `gh pr review` write must carry one of the two non-approving
# verdict flags (or it is treated as an unrecognized-spelling approval), AND
# must not also carry a recognized --approve spelling -- carrying both is
# denied rather than trusting gh to reject the combination.
if [[ "$COMMAND_FLAT" =~ $PATTERN_PR_REVIEW_CMD ]]; then
  if ! [[ "$COMMAND_FLAT" =~ $PATTERN_REVIEW_PR_VERDICT_FLAG ]] || [[ "$COMMAND_FLAT" =~ $PATTERN_REVIEW_PR_APPROVE_FLAG_CLI ]]; then
    REVIEW_PR_WRITE_AUTHORIZED=0
  fi
fi
# REST form: denylist on the fixed `event=APPROVE` vocabulary.
if [[ "$COMMAND_FLAT" =~ $PATTERN_REVIEW_PR_API_EVENT_APPROVE ]]; then
  REVIEW_PR_WRITE_AUTHORIZED=0
fi

# The cross-repo bypass below releases reads only. It exists so that research
# on an external repo is not mistaken for a PR response here, and research is
# a read; there is no legitimate cross-repo write it needs to permit, because
# the attribution the gate protects is owed to readers of any public PR, not
# only this repo's.
#
# Confining it to reads is also what makes it safe to decide on substring
# evidence. Both extractions below scan the whole command for a repo-shaped
# token, so any text in the call — including a comment body the model was
# induced to write — can supply one. While a write can be released that way,
# a decoy reference to another repo anywhere in the command hands back an
# unattributed write to this one; a decoy that only releases a read costs
# nothing, since the read was never the thing being protected.
if [ "$GATED_WRITE" -eq 1 ]; then
  if [ "$REVIEW_PR_WRITE_AUTHORIZED" -eq 1 ]; then
    exit 0
  fi
  emit_deny "PR/issue comment write blocked by respond-pr gate. Writes are denied for every repo, not only the current one, because the [Claude Code] attribution prefix that discloses AI authorship is owed to readers of any public thread. For a comment on the CURRENT branch's PR: run the /respond-pr skill, which applies that prefix — do not ask the user for permission, just run it. For a comment on any OTHER repo or on an unrelated PR: /respond-pr cannot service that; it scopes to the current branch's PR. For posting a /review-pr review: this command must exactly match the reviewed PR/HEAD/body recorded by /review-pr's own completion marker — re-run the skill through step 9 rather than hand-constructing the gh call. Stop and ask the user how they want to proceed."
  exit 0
fi

# Cross-repo bypass: if the command explicitly targets a repo that differs
# from the current git origin, it is research on an external repo (e.g.
# reading anthropics/claude-code issues while working in the user's project),
# not a PR response in the current repo. Two explicit forms are recognized:
#   gh api repos/OWNER/REPO/...
#   gh pr <cmd> ... -R OWNER/REPO      (also --repo OWNER/REPO, --repo=OWNER/REPO)
# Implicit commands (no repo specified) still gate — gh resolves those
# against the current repo. Caveats: (1) in-repo reads of an actual Issue
# (not a PR) still false-positive; accepted because the user does not track
# work in GitHub Issues. (2) both extractions scan raw command text, so a
# repo-shaped or flag-shaped substring inside a quoted body can spoof a
# cross-repo match; per the write check above, the most that spoof can
# release is a read. (3) the -R/--repo form is reachable only if a future
# arm gates a read issued through `gh pr`; every `gh pr` form gated today is
# a write and stops above.
# Both extractions read COMMAND_FLAT, not COMMAND. `sed` is per-line exactly as
# grep is, so a wrapped cross-repo URL would otherwise be invisible here while
# the arm chain above already saw it — the two would disagree about the same
# command. That direction fails closed (the repo goes unrecognized and the
# command denies), but a gate whose two halves read different text is a gate
# whose behaviour cannot be reasoned about from either half alone.
COMMAND_REPO=$(printf '%s\n' "$COMMAND_FLAT" | sed -nE 's#.*repos/([^/]+/[^/]+)/(pulls|issues)/[0-9]+/(comments|reviews).*#\1#p;s#.*repos/([^/]+/[^/]+)/(pulls|issues)/comments/[0-9]+.*#\1#p' | head -1)
if [ -z "$COMMAND_REPO" ]; then
  COMMAND_REPO=$(printf '%s\n' "$COMMAND_FLAT" | sed -nE 's#.*[[:space:]](-R|--repo)[[:space:]=]+([^[:space:]=]+/[^[:space:]]+).*#\2#p' | head -1)
fi

# `gh api repos/{owner}/{repo}/...` is documented gh syntax: gh substitutes
# the current repo at call time. The placeholder is literal text, so it reads
# as a repo name unlike any origin and would otherwise release the very
# same-repo access this gate exists to catch.
if [[ "$COMMAND_REPO" == *[{}]* ]]; then
  COMMAND_REPO=""
fi

if [ -n "$COMMAND_REPO" ]; then
  # _lib_capped, not bare git: a stale index lock or a network-mounted .git
  # would otherwise block this call — and with it every gated Bash tool call
  # in the session — for as long as the filesystem takes to answer.
  CURRENT_URL=$(_lib_capped git config --get remote.origin.url 2>/dev/null)
  if [ -n "$CURRENT_URL" ]; then
    CURRENT_REPO=$(printf '%s\n' "$CURRENT_URL" | sed -nE 's#.*[:/]([^/:]+/[^/]+)$#\1#p' | sed 's#\.git$##')
    if [ -n "$CURRENT_REPO" ] && [ "$COMMAND_REPO" != "$CURRENT_REPO" ]; then
      exit 0
    fi
  fi
fi

emit_deny "PR comment access blocked by respond-pr gate. Run the /respond-pr skill instead — it fetches inline file comments, top-level review bodies, AND issue-level comments (Claude habitually fetches only the first and misses real feedback), and it enforces the [Claude Code] attribution prefix on replies so comments posted through the GitHub token are clearly labeled as AI-generated. Do not ask the user for permission — run /respond-pr and let it handle this operation."
