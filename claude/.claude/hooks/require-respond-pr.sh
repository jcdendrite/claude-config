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
# Orphaned markers (from sessions that errored before cleanup) are evicted
# automatically: the hook checks kill -0 on the stored PID; dead PID → rm.
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

# Bypass: fresh marker for THIS session's session_id means we're inside the
# skill and should let its own gh commands through. Empty session_id (older
# Claude Code versions, payload-schema drift) falls through to the gate.
SESSION_ID=$(printf '%s\n' "$INPUT" | _lib_jq -r '.session_id // empty')
if [ -n "$SESSION_ID" ]; then
  MARKER="$HOME/.claude/.respond-pr-active.d/$SESSION_ID"
  if [ -f "$MARKER" ]; then
    STORED_PID=$(cat "$MARKER" 2>/dev/null | tr -d '[:space:]')
    if [[ "$STORED_PID" =~ ^[0-9]+$ ]] && kill -0 "$STORED_PID" 2>/dev/null; then
      exit 0
    fi
    rm -f "$MARKER" 2>/dev/null
  fi
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
# Parameter expansion, not `printf | tr`: a subshell pipeline that fails to
# exec leaves COMMAND_FLAT empty, every arm below misses, and the gate falls
# through to allow — a fail-open in a fail-closed gate. This form cannot fail
# and spares the forks on a hook that fires on every Bash call.
COMMAND_UNWRAPPED=${COMMAND//\\$'\n'/}
COMMAND_FLAT=${COMMAND_UNWRAPPED//$'\n'/ }

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
  emit_deny "PR/issue comment write blocked by respond-pr gate. Writes are denied for every repo, not only the current one, because the [Claude Code] attribution prefix that discloses AI authorship is owed to readers of any public thread. For a comment on the CURRENT branch's PR: run the /respond-pr skill, which applies that prefix — do not ask the user for permission, just run it. For a comment on any OTHER repo or on an unrelated PR: /respond-pr cannot service that; it scopes to the current branch's PR. Stop and ask the user how they want to proceed."
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
