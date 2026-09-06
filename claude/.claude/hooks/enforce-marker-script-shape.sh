#!/bin/bash
# hook-class: gate
# Gate: guard review-marker state. Two jobs:
#   1. Deny gate-releasing writes (a marker file path via Write/Edit/MultiEdit,
#      or `marker.sh write|activate` via Bash) from agent types that cannot
#      have run the review a gate demands.
#   2. Enforce strict invocation shape for ~/.claude/scripts/marker.sh.
#
# Wired on both the Bash and Write|Edit|MultiEdit PreToolUse matchers; job 1
# needs both surfaces, since gating only the shell leaves a direct file write
# as an open path to the same state. Neither matcher carries an `if` condition,
# so there is no settings.json-vs-internal-filter drift to keep in sync; the
# tool-name case below is the authoritative filter.
#
# Fail posture: fail-closed — if jq cannot parse the input, deny.
#
# Known gaps this hook does NOT close:
#   - The Bash arm matches command text, so shell indirection that separates
#     `marker.sh` from the op keyword (variable or function wrapper) is not
#     caught. Documented at that check; the path-based arm is what makes the
#     gate-release property hold regardless.
#   - A Bash-tool write to a marker path via `>`/`>>`/`&>`/`&>>`/`<>`, `tee`,
#     `cp`/`mv`/`install` (last-argument form), `dd of=`, or `sed -i` is
#     caught by a dedicated scan that runs before Stage 1, independent of the
#     command mentioning `marker.sh`. Still open: `>|` (clobber-override --
#     its literal `|` gets severed from the operator by the fragment
#     splitter this scan reuses, before extraction ever sees a whole token);
#     `python3 -c "open(...).write(...)"` and here-doc bodies handed to an
#     interpreter; a `$(...)`-computed target path; shell-function/variable
#     indirection around the write utility itself; `cp`/`mv`/`install -t DIR`
#     or `--target-directory=DIR` (destination isn't the last argument, so
#     the last-argument heuristic misses it); a symlink whose own path text
#     carries no literal `.claude` (e.g. `ln -s ~/.claude/code-review-markers
#     /tmp/x`, then `printf ... > /tmp/x/forged`) — this scan's fast-reject
#     requires that literal, unlike the Write/Edit arm's unconditional
#     realpath resolution; and, beyond the first
#     `$MARKER_WRITE_REALPATH_BUDGET` `.claude`-mentioning candidates in one
#     command, a `..`-traversal or stow-fold-physical-path obfuscation --
#     candidates past the budget are shape-tested against their raw
#     tilde-expanded form only, not realpath-normalized, bounding per-fire
#     cost against a many-target `tee`/`cp`/`mv`/`install` invocation; and a
#     CLAUDE_CONFIG_DIR with no `.claude` path segment (`_marker_shape_match`'s
#     config-dir-aware shape, added for the Write/Edit/MultiEdit arm below) —
#     both this scan's Stage-0 command-level pre-filter and its per-candidate
#     `_marker_write_candidate_mentions_claude` filter require a literal
#     `.claude` substring before a candidate ever reaches `_marker_shape_match`,
#     so a config-dir-resolved marker write with no such substring anywhere
#     in the command is never scanned at all. The Write/Edit/MultiEdit arm has
#     no such pre-filter (it always resolves its one target), so it is not
#     affected. Closing this means loosening a pre-filter deliberately kept
#     subprocess-free for per-fire cost — out of scope here.
#   - `deactivate` / `clear-stale` are ungated for every agent type (they
#     re-arm gates rather than release them).
#   - Marker state reached by a tool other than Bash/Write/Edit/MultiEdit
#     (none exists today) would be ungated.
#   - Both arms key on `.agent_type`, which the harness populates only for
#     subagents it dispatches. A nested top-level session shelled out of a
#     Bash tool call (`claude -p ...`) would carry no agent_type and read as
#     the main session. Unconfirmed whether that is reachable from a subagent's
#     execution context; if it is, every agent-identity-keyed hook shares it
#     (deny-reviewer-tree-mutation.sh has the same dependency), so the fix
#     belongs at the permission layer for the whole class rather than here.
#   - MARKER_WRITE_COMMAND_UNQUOTED's sed/tr strip and
#     _bash_marker_redirect_candidates's own _lib_split_fragments call both
#     check their exit status and fail closed, matching
#     deny-network-installs.sh's COMMAND_UNQUOTED_EXIT/FRAGMENTS_SPLIT_EXIT
#     pattern.
#
# WARNING: Do NOT remove the internal marker.sh check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands. The internal grep is the actual
# gate. The "if" field is a hint only.
#
# Commands that start directly with the marker.sh path (~/ or absolute) must
# match one of the 19 single-command shapes, the marker.sh write chain to git
# commit, or a chain of two-or-more valid marker.sh shapes joined by `&&`
# (any op/target combination) — equivalent to running each op separately,
# since every marker operation is independently allowlisted or harmless. No
# redirects (except trailing `2>/dev/null`), no extra args. Wrapped forms
# (env-var prefix, bash wrapper, relative path, subshell) are not gated here —
# they fast-exit at Stage 2 and are denied by
# the permissions.allow layer, which does not list their wrapper executables.
# Removing the permissions.allow gate without updating this hook would leave
# those forms ungated.
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
  emit_deny "Blocked by marker-script-shape gate: could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "Blocked: could not parse tool-input JSON."

# ---------------------------------------------------------------------------
# Gate-release authority
# ---------------------------------------------------------------------------
# A review gate may be released only by a caller that could have run the
# review. No agent in _LIB_NO_GATE_RELEASE_AGENTS could have: most carry no
# `Skill` tool and cannot invoke a review skill at all, and the one harness
# built-in that does carry it (`Plan`) is dispatched read-only by mandate.
# Either way a marker written by one asserts a review that could not
# have happened. marker.sh resolves session_id by walking
# the process ancestor chain to the Claude main process, so such a write is
# indistinguishable from the parent session's and releases the gate for the
# whole session, not just the subagent.
#
# The control keys on MARKER STATE, not on command text. Marker state is
# reachable two ways, and both are gated below because gating only one leaves
# the property false:
#   - the Write/Edit/MultiEdit tools, writing a marker file path directly;
#   - a Bash call invoking marker.sh.
# The Write/Edit arm is the load-bearing one: it matches on the resolved target
# path, so no shell-level indirection can evade it. The Bash arm necessarily
# matches command text and inherits that surface's limits — see its own note.
#
GATE_RELEASE_DENIAL_GUIDANCE="Releasing a gate requires having run the review that the gate demands, and this agent type could not have run it — either it carries no Skill tool and cannot invoke a review skill at all, or it is dispatched read-only by mandate. Either way a marker it writes would assert a review that never happened. Marker writes are attributed to the parent session, so this would release the gate for the whole session, not just this subagent.

Report the denial to the dispatching session instead: name the gate that blocked you, the command or path it blocked, and what you had completed. The dispatching session runs the review skill (or delegates it to a general-purpose subagent, which does carry Skill) and re-dispatches you.

Matching a hash you computed yourself is not authorization — an equal hash shows the state is unchanged, not that anyone reviewed it."

# _marker_shape_match TARGET_PATH [ALLOW_REALPATH=1]
# True (exit 0) iff TARGET_PATH matches the marker-directory SHAPE, tested via
# the raw tilde-expansion and its `_lib_realpath_m` normalization, so both
# call sites (Write/Edit/MultiEdit's single target below, the Bash redirect/
# utility arm's several extracted targets further down) share one pattern
# that cannot drift between them. Exit 1: no match. Exit 2: the Claude Code
# config directory could not be resolved, so a config-dir-relative marker
# alias could not be ruled out — callers must deny, not skip, on exit 2.
# Shape test only: no agent-type read, no deny decision — callers decide what
# a match or a resolution failure means.
#
# Shape-anchored, not $HOME-prefixed: stow-fold makes the same marker also
# reachable at <repo>/claude/.claude/<kind>-markers/, which has no $HOME
# segment, and a `..` segment doesn't carry a literal $HOME/.claude/ prefix
# until normalized. A second, independent shape covers CLAUDE_CONFIG_DIR
# values with no `.claude` segment at all (e.g. ~/.config/claude-accounts/
# <account>), which the $HOME-relative shape above cannot see.
# The raw candidate is always tested too, since `_lib_realpath_m` can return
# empty under `_lib_capped`'s timeout on a stalled $HOME mount.
# `realpath` is still required to catch a symlink whose own path carries no
# marker-shaped segment but resolves into the markers directory — the same
# reasoning applies to the config dir itself, so it is realpath'd too.
# Over-matching is safe: a false match only denies an agent that could never
# legitimately release a gate.
#
# The config-dir branch runs only when CLAUDE_CONFIG_DIR is actually set:
# _lib_config_dir()'s fallback (unset CLAUDE_CONFIG_DIR) resolves to exactly
# $HOME/.claude, a strict subset of the $HOME-relative shape test below —
# a candidate that would match the config-dir-anchored pattern in that
# default case necessarily already matches the $HOME-relative one, so
# running it would only add a redundant `_lib_config_dir`/`_lib_realpath_m`
# call for the overwhelming majority of installations that never set
# CLAUDE_CONFIG_DIR. When it IS set, resolution and its realpath follow the
# same ALLOW_REALPATH gating as the $HOME-relative candidate: the raw
# resolved value is always tested, only its realpath normalization is
# budget-gated, so a budget-exhausted candidate degrades the same way the
# $HOME-relative shape does rather than losing config-dir coverage entirely.
# A resolution failure denies (return 2) unconditionally once CLAUDE_CONFIG_DIR
# is set, for the same reason the Write/Edit/MultiEdit arm denies
# unconditionally: an unresolvable config dir means no candidate here can be
# verified as NOT a review-marker path, independent of the realpath budget.
_marker_shape_match() {
  local target_path="$1" allow_realpath="${2:-1}"
  local expanded normalized candidate matched=1
  local config_dir_resolved="" config_dir_realpath=""
  expanded="${target_path/#\~/$HOME}"
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    if ! config_dir_resolved=$(_lib_config_dir 2>/dev/null); then
      return 2
    fi
    if [ "$allow_realpath" = "1" ]; then
      config_dir_realpath=$(_lib_realpath_m "$config_dir_resolved" 2>/dev/null)
    fi
  fi
  if [ "$allow_realpath" = "1" ]; then
    normalized=$(_lib_realpath_m "$expanded" 2>/dev/null)
  else
    normalized=""
  fi
  for candidate in "$expanded" "$normalized"; do
    [ -n "$candidate" ] || continue
    # nocasematch: macOS's default APFS volume is case-insensitive, so a
    # case-varied marker path (~/.Claude/...) resolves to the same on-disk
    # file this case-sensitive pattern would otherwise miss. Scoped tightly
    # around this one case statement and restored immediately after -- this
    # function runs again per candidate, so the shopt must not leak between
    # iterations.
    shopt -s nocasematch
    # Completion markers (<kind>-markers/) release a gate outright.
    # Active-bypass markers (.<kind>-active.d/) suspend one, honored by the
    # plan gate with no hash comparison at all.
    case "$candidate" in
      */.claude/*-markers/*|*/.claude/.*-active.d/*) matched=0 ;;
    esac
    if [ "$matched" -ne 0 ] && [ -n "$config_dir_resolved" ]; then
      case "$candidate" in
        "$config_dir_resolved"/*-markers/*|"$config_dir_resolved"/.*-active.d/*) matched=0 ;;
      esac
    fi
    if [ "$matched" -ne 0 ] && [ -n "$config_dir_realpath" ]; then
      case "$candidate" in
        "$config_dir_realpath"/*-markers/*|"$config_dir_realpath"/.*-active.d/*) matched=0 ;;
      esac
    fi
    shopt -u nocasematch
    [ "$matched" -eq 0 ] && break
  done
  return "$matched"
}

# Defense-in-depth: filter on tool name here rather than relying on the
# settings.json matchers alone. Anything that is neither a file-write tool nor
# Bash cannot reach marker state.
#
# Per-fire cost is why the two arms read .agent_type at different points. This
# hook fires on EVERY Write/Edit/MultiEdit and EVERY Bash call, so neither arm
# may spend more than it must. The Bash arm defers reading .agent_type until
# after Stage 1's substring test, which rejects the overwhelming majority of
# Bash calls for free. The file-write arm uses the in-shell key test below for
# the same purpose, then reads .agent_type and .file_path together in a single
# jq call.
case "$TOOL_NAME" in
  Write|Edit|MultiEdit)
    # Path-based arm. Every marker lives under a known directory, so the
    # decision is "is this agent writing marker state?" — a question the
    # resolved path answers directly, with no command text to outsmart.
    #
    # In-shell fast-reject before spending a subprocess: this arm can only ever
    # deny a subagent, so a payload with no agent_type key at all is the main
    # session and needs no jq. The harness — not the model — serializes the
    # enclosing object; a subagent controls only .tool_input, so it cannot
    # reorder, reformat, or omit this key to dodge the test. A payload that
    # contains the literal string elsewhere falls through to the authoritative
    # jq read below, so this cannot produce a false allow.
    #
    # Both this test and the jq parse are linear in payload size — $INPUT holds
    # the full file CONTENT on a Write — so there is no constant-time option
    # here, only a choice of constant. Measured on a 5MB Write against a
    # 684ms parse-only floor: this test adds ~162ms, the jq call it skips adds
    # ~345ms. Keep the cheap test first; do not "simplify" it away, and do not
    # compare it against a hypothetical `grep` that would itself cost a
    # subprocess to avoid one.
    case "$INPUT" in
      *'"agent_type"'*) ;;
      *) exit 0 ;;
    esac
    #
    #
    # .agent_type is the trust-boundary field the decision hinges on, so read
    # it fail-closed. An unchecked read leaves it empty on a jq failure and
    # falls through to the main-session path — the one fail-OPEN branch in an
    # otherwise fail-closed hook. Both fields come back from a single jq call
    # delimited by ASCII Unit Separator (0x1f), the same technique and the same
    # reasoning as _lib_parse_tool_input_or_deny: one subprocess, and a
    # delimiter that cannot occur in a real agent type or path.
    if ! AGENT_AND_PATH=$(printf '%s\n' "$INPUT" \
      | _lib_jq -r '"\(.agent_type // "")\u001f\(.tool_input.file_path // "")"' 2>/dev/null); then
      emit_deny "Blocked by marker-script-shape gate: could not read .agent_type and .tool_input.file_path from the tool payload — refusing to evaluate gate-release authority under unreadable trust-boundary fields."
      exit 0
    fi
    AGENT_TYPE="${AGENT_AND_PATH%%$'\x1f'*}"
    TARGET_PATH="${AGENT_AND_PATH#*$'\x1f'}"

    _lib_is_no_gate_release_agent "$AGENT_TYPE" || exit 0
    [ -n "$TARGET_PATH" ] || exit 0

    # Shape-tested via _marker_shape_match (defined above; shared with the
    # Bash redirect/utility arm below, including its config-dir-resolution-
    # failure exit code) — see its comment for the pattern rationale.
    _marker_shape_match "$TARGET_PATH"
    case "$?" in
      0)
        emit_deny "Marker write denied: the '$AGENT_TYPE' agent cannot release a review gate by writing '$TARGET_PATH'.

$GATE_RELEASE_DENIAL_GUIDANCE"
        ;;
      2)
        emit_deny "Marker write denied: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) to verify '$TARGET_PATH' is not a review-marker path."
        ;;
    esac
    exit 0
    ;;
  Bash) ;;
  *) exit 0 ;;
esac

# _bash_marker_fragment_candidates FRAGMENT
# Emits, one per line, every write-target word in FRAGMENT worth
# shape-testing: `>`/`>>` operands (bare or glued, fd-prefixed), `tee`
# arguments, `cp`/`mv`/`install` last arguments, `dd of=` glued arguments,
# and `sed -i` last arguments. Over-emission is safe — each candidate is
# independently shape-tested by _marker_shape_match.
_bash_marker_fragment_candidates() {
  local fragment="$1"
  local saved_opts=$-
  set -f
  # Capitalized (unlike this file's other locals): bash's array-length
  # operator on the lowercase name reads as a Slack-channel-shaped reference
  # to this repo's own redaction detector.
  local -a Words=()
  local word
  for word in $fragment; do
    Words+=("$word")
  done
  if [[ "$saved_opts" != *f* ]]; then set +f; fi

  local n=${#Words[@]}
  [ "$n" -gt 0 ] || return 0

  # Mirrors deny-network-installs.sh:84-91's redirect_op_re/redirect_glued_re
  # construction: fd-prefixed `>`/`>>`/`<>`, or unprefixed `&>`/`&>>` (bash's
  # combined stdout+stderr redirect, which cannot take an fd prefix),
  # standalone (next word is the target) or glued to it in one token. `>|`
  # (clobber-override) is deliberately excluded: it contains a literal `|`,
  # which _lib_split_fragments (the fragment splitter this scan already
  # calls) treats as a pipeline separator, severing the operator from its
  # target before this function ever sees a whole token -- a candidate would
  # ship silently unmatched, not silently over-matched.
  local redirect_op_re='^([0-9]*(>>|<>|>)|&>>|&>)$'
  local redirect_glued_re='^([0-9]*(>>|<>|>)|&>>|&>)([^[:space:]].*)$'
  local i
  for ((i = 0; i < n; i++)); do
    word="${Words[$i]}"
    # Exact-operator test first: for a standalone `>>`, redirect_glued_re
    # would otherwise backtrack its (>>|>|...) alternation down to `>` and
    # misread the second `>` as a one-character glued target.
    if [[ "$word" =~ $redirect_op_re ]]; then
      [ $((i + 1)) -lt "$n" ] && printf '%s\n' "${Words[$((i + 1))]}"
    elif [[ "$word" =~ $redirect_glued_re ]]; then
      printf '%s\n' "${BASH_REMATCH[3]}"
    fi
  done

  if _lib_fragment_invokes_tool "$fragment" tee; then
    local seen_tee=false
    for word in "${Words[@]}"; do
      if ! $seen_tee; then
        [ "${word##*/}" = "tee" ] && seen_tee=true
        continue
      fi
      case "$word" in
        -*) ;;
        *) printf '%s\n' "$word" ;;
      esac
    done
  fi

  if _lib_fragment_invokes_tool "$fragment" cp \
    || _lib_fragment_invokes_tool "$fragment" mv \
    || _lib_fragment_invokes_tool "$fragment" install; then
    printf '%s\n' "${Words[$((n - 1))]}"
  fi

  if _lib_fragment_invokes_tool "$fragment" dd; then
    for word in "${Words[@]}"; do
      case "$word" in
        # Substring offset, not a `#`-prefix strip: the latter's literal
        # "of=" reads as a Slack-channel-shaped reference to this repo's own
        # redaction detector. offset 3 skips exactly "of=", matched above.
        of=*) printf '%s\n' "${word:3}" ;;
      esac
    done
  fi

  if _lib_fragment_invokes_tool "$fragment" sed; then
    for word in "${Words[@]}"; do
      case "$word" in
        -i*) printf '%s\n' "${Words[$((n - 1))]}"; break ;;
      esac
    done
  fi
}

# _bash_marker_redirect_candidates COMMAND_UNQUOTED
# Splits COMMAND_UNQUOTED into fragments (the same split _lib_split_fragments
# gives deny-network-installs.sh) and emits every fragment's candidate
# write-target words, one per line. Returns _lib_split_fragments's own exit
# status on failure -- the caller checks it and denies at the top level;
# see the comment below for why this function cannot emit_deny itself.
_bash_marker_redirect_candidates() {
  local command_unquoted="$1" fragment
  local fragments fragments_split_exit
  # Checked and fail-closed, matching deny-network-installs.sh's
  # FRAGMENTS_SPLIT_EXIT pattern. Surfaced via return rather than emit_deny:
  # this function is invoked inside the caller's own $(...) command
  # substitution, so an emit_deny here would exit only that subshell, not
  # the hook process -- a silently-empty candidate list would fall through
  # to this scan's normal "no match" allow with no bypass valve.
  fragments=$(_lib_split_fragments "$command_unquoted")
  fragments_split_exit=$?
  if [ "$fragments_split_exit" -ne 0 ]; then
    return "$fragments_split_exit"
  fi
  # Here-string, not process substitution: _lib_split_fragments emits no
  # trailing newline, and `<<<` always appends exactly one, so `read` doesn't
  # silently drop a single/final fragment at EOF.
  while IFS= read -r fragment; do
    [ -n "$fragment" ] || continue
    _bash_marker_fragment_candidates "$fragment"
  done <<< "$fragments"
}

# _marker_write_candidate_mentions_claude CANDIDATE
# Cheap, subprocess-free pre-filter run before the expensive _marker_shape_match
# resolution: a candidate whose raw text carries no .claude segment at all
# cannot match except via the symlink-aliasing residual this scan already
# accepts (see header), so skipping realpath for it adds no new gap. Bounds
# per-fire cost on a many-target `tee` invocation, which otherwise pays one
# realpath subprocess per destination argument regardless of relevance.
_marker_write_candidate_mentions_claude() {
  local candidate="$1" mentions=1
  shopt -s nocasematch
  case "$candidate" in
    *.claude*) mentions=0 ;;
  esac
  shopt -u nocasematch
  return "$mentions"
}

# Per-fire cost on every Bash call, not just a marker-shaped one: the
# quote-strip below is 2 forks (_lib_strip_shell_quotes's sed + tr) and the
# '.claude' pre-filter is 1 more, so this arm adds 3 forks ahead of Stage 1's
# own single-grep fast-reject regardless of relevance. Necessary ordering,
# not a simplification target: this scan exists specifically to catch a
# command that never reaches Stage 1's marker.sh substring check, so it
# cannot run after that check without reopening the bypass it closes.
#
# Bash-tool write to a marker path via a redirect or write utility that never
# mentions `marker.sh` — closes the class of bypass Stage 1's substring gate
# below would otherwise fast-exit as an allow. Runs first for that reason.
# Fast-reject mirrors Stage 1's own cheap-prefilter discipline: every alias
# of a marker path contains the literal '.claude'. Case-insensitive
# (grep -qiF) for the same reason _marker_shape_match's case pattern is:
# macOS's default APFS volume is case-insensitive. Runs against the
# quote-stripped command, not raw $COMMAND, so `~/.cla''ude/...` (which the
# shell collapses to `.claude` at execution time) can't skip this fast-reject
# by never containing a contiguous `.claude` substring in its raw text.
#
# Per-candidate cost: the pre-filter itself adds no subprocess, so rejecting
# a candidate costs nothing beyond the case-statement test. A candidate that
# reaches _marker_shape_match's realpath resolution costs one
# `_lib_realpath_m` call in the default configuration (CLAUDE_CONFIG_DIR
# unset), or two (target path, then config dir) once CLAUDE_CONFIG_DIR is
# set -- _marker_shape_match skips the config-dir branch entirely in the
# default case, since it would be redundant with the $HOME-relative shape
# test. MARKER_WRITE_REALPATH_BUDGET below bounds how many candidates in one
# fire pay that cost, capping worst-case added latency at roughly
# budget * (1 or 2 realpath calls, depending on CLAUDE_CONFIG_DIR) rather
# than scaling with an attacker- or user-controlled candidate count;
# absolute per-call latency is too load-dependent on a shared machine to
# state as a fixed ms figure here. A future edit that removes the
# pre-filter, raises the budget, or adds another per-candidate subprocess
# call should re-derive this
# call-count accounting.
MARKER_WRITE_COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
MARKER_WRITE_COMMAND_UNQUOTED_EXIT=$?
if [ "$MARKER_WRITE_COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "Blocked by marker-script-shape gate: could not quote-strip the command text (exit ${MARKER_WRITE_COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach marker state."
  exit 0
fi
if printf '%s' "$MARKER_WRITE_COMMAND_UNQUOTED" | grep -qiF '.claude'; then
  MARKER_WRITE_REDIRECT_CANDIDATES=$(_bash_marker_redirect_candidates "$MARKER_WRITE_COMMAND_UNQUOTED")
  MARKER_WRITE_REDIRECT_CANDIDATES_EXIT=$?
  if [ "$MARKER_WRITE_REDIRECT_CANDIDATES_EXIT" -ne 0 ]; then
    emit_deny "Blocked by marker-script-shape gate: could not split the command into fragments (exit ${MARKER_WRITE_REDIRECT_CANDIDATES_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach marker state."
    exit 0
  fi
  MARKER_WRITE_AGENT_CHECKED=false
  MARKER_WRITE_REALPATH_BUDGET=10
  while IFS= read -r MARKER_WRITE_CANDIDATE; do
    [ -n "$MARKER_WRITE_CANDIDATE" ] || continue
    _marker_write_candidate_mentions_claude "$MARKER_WRITE_CANDIDATE" || continue
    if [ "$MARKER_WRITE_REALPATH_BUDGET" -gt 0 ]; then
      MARKER_WRITE_ALLOW_REALPATH=1
      MARKER_WRITE_REALPATH_BUDGET=$((MARKER_WRITE_REALPATH_BUDGET - 1))
    else
      MARKER_WRITE_ALLOW_REALPATH=0
    fi
    _marker_shape_match "$MARKER_WRITE_CANDIDATE" "$MARKER_WRITE_ALLOW_REALPATH"
    MARKER_WRITE_SHAPE_STATUS=$?
    if [ "$MARKER_WRITE_SHAPE_STATUS" -eq 2 ]; then
      MARKER_WRITE_CANDIDATE_TRUNCATED=$(printf '%s' "$MARKER_WRITE_CANDIDATE" | cut -c1-80)
      emit_deny "Marker write denied: could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) to verify '$MARKER_WRITE_CANDIDATE_TRUNCATED' is not a review-marker path."
      exit 0
    fi
    [ "$MARKER_WRITE_SHAPE_STATUS" -eq 0 ] || continue
    # .agent_type is read here, not up front, so the common case — zero
    # shape-matching candidates — never spends a subprocess on it; unlike the
    # Write/Edit arm above, this jq call is not unavoidable.
    if ! $MARKER_WRITE_AGENT_CHECKED; then
      if ! AGENT_TYPE=$(printf '%s\n' "$INPUT" | _lib_jq -r '.agent_type // empty' 2>/dev/null); then
        emit_deny "Blocked by marker-script-shape gate: could not read .agent_type from the tool payload — refusing to evaluate gate-release authority under an unreadable trust-boundary field."
        exit 0
      fi
      MARKER_WRITE_AGENT_CHECKED=true
    fi
    if _lib_is_no_gate_release_agent "$AGENT_TYPE"; then
      MARKER_WRITE_CANDIDATE_TRUNCATED=$(printf '%s' "$MARKER_WRITE_CANDIDATE" | cut -c1-80)
      emit_deny "Marker write denied: the '$AGENT_TYPE' agent cannot release a review gate by writing '$MARKER_WRITE_CANDIDATE_TRUNCATED'.

$GATE_RELEASE_DENIAL_GUIDANCE"
      exit 0
    fi
  # Here-string over the already-captured MARKER_WRITE_REDIRECT_CANDIDATES,
  # not a nested command substitution: the split's exit status is checked
  # above, before this loop starts, matching
  # _bash_marker_redirect_candidates's own inner loop.
  done <<< "$MARKER_WRITE_REDIRECT_CANDIDATES"
fi

# Strip leading/trailing whitespace — computed before the activation guards so
# both the fast-reject and anchored-path check share one computation.
TRIMMED=$(printf '%s' "$COMMAND" | sed -E 's/^[[:space:]]+//')

# Stage 1: cheap substring fast-reject — most Bash calls have no marker mention.
printf '%s' "$COMMAND" | grep -qF 'marker.sh' || exit 0

# Bash arm of the gate-release authority check. Placed immediately after
# Stage 1 and BEFORE Stage 2, deliberately: Stage 2 fast-exits wrapped forms
# (bash -c, env-var prefix, relative path) and leaves them to
# permissions.allow, so a check placed after it would inherit that hole.
# Matching the op keyword anywhere in the command catches tilde, absolute,
# relative, chained, and wrapped invocations.
#
# SCOPE LIMIT, stated rather than implied: this arm matches command TEXT, so it
# only fires while `marker.sh` and the op keyword stay textually adjacent.
# Shell-level indirection that breaks that adjacency — assigning the path to a
# variable and invoking through it, or wrapping the call in a shell function —
# is not matched here, the same carve-out Stage 2 already documents for wrapped
# forms. Those forms are not pre-approved in permissions.allow either, so they
# surface as a permission prompt rather than a silent allow. The path-based
# Write/Edit arm above is what makes the overall property hold; do not read
# this arm as a complete boundary on its own.
#
# Accepted false-deny: a review-only agent grepping for the literal string
# `marker.sh write` while reviewing this repo is denied. Matching the op
# keyword rather than the bare tool name keeps plain `grep -rn marker.sh`
# available, which is the common reviewer action.
#
# `deactivate` and `clear-stale` are not gated — they re-arm gates rather than
# releasing them. That is directionally safe but not free: a mandate-scoped
# subagent calling `deactivate` clears the PARENT session's active-bypass
# marker (same ancestor walk) and could disrupt a review running outside its
# own turn. Narrow enough to accept; noted so the omission reads as a decision.
#
# .agent_type is read here rather than at the top of the hook so that the
# overwhelming majority of Bash calls — the ones Stage 1 already rejected —
# never spend a subprocess on it. Read fail-closed: an unchecked read leaves it
# empty on a jq failure and falls through to the main-session path.
if ! AGENT_TYPE=$(printf '%s\n' "$INPUT" | _lib_jq -r '.agent_type // empty' 2>/dev/null); then
  emit_deny "Blocked by marker-script-shape gate: could not read .agent_type from the tool payload — refusing to evaluate gate-release authority under an unreadable trust-boundary field."
  exit 0
fi

if _lib_is_no_gate_release_agent "$AGENT_TYPE" \
  && printf '%s' "$COMMAND" | grep -qE 'marker\.sh[[:space:]]+(write|activate)'; then
  emit_deny "Marker write denied: the '$AGENT_TYPE' agent cannot release a review gate.

$GATE_RELEASE_DENIAL_GUIDANCE"
  exit 0
fi

# Reject path traversal sequences before the allowlist check. The VALID_PATTERN
# character class permits '.' and '/', which together admit '../' segments.
# Match '..' only as a path segment (../foo, foo/.., foo/../bar) — not as
# range notation (a..b), ellipses, or node_modules/.../foo. This check runs
# before Stage 2 so that tilde-form traversal paths (e.g.
# ~/.claude/scripts/../scripts/marker.sh) are caught even though Stage 2's
# anchored regex does not match them.
if printf '%s' "$TRIMMED" | grep -qE '(^|/)\.\.(/|$)'; then
  TRUNCATED=$(printf '%s' "$TRIMMED" | cut -c1-80)
  emit_deny "marker.sh invocation denied (path traversal '..' detected). Command (truncated): $TRUNCATED"
  exit 0
fi

# Stage 2: anchored leading-path check. Bash =~ treats the subject as a single
# string; `^` anchors at position 0 only — correct for multi-line $COMMAND
# (heredocs) because grep -E with '^' matches per-line and would over-activate
# on a heredoc body whose inner line starts with the script path.
# Wrapped/chained forms (bash -c, env-var prefix, semicolons, subshells)
# intentionally fast-exit here; permissions.allow is their gate — those wrapper
# executables are not in the allow list, so the permission layer denies them
# before this hook's deep validation would ever matter.
if [[ ! "$TRIMMED" =~ ^(\~|\$HOME|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh([[:space:]]|$) ]]; then
  exit 0
fi

# Path prefix + one valid (op, target) shape — no anchors, no trailing
# suffix. Shared building block for VALID_PATTERN and the marker-chain
# pattern below, so the path-prefix regex fragment has one authoritative copy.
MARKER_SHAPE='(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+(write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)|(activate|deactivate)[[:space:]]+(plan-review|ready-for-review|respond-pr|memory-skill|handoff)|clear-stale([[:space:]]+--dry-run)?|resolve-session-id|status|check[[:space:]]+code-review)'

# Strict allowlist. Tilde form (~/.claude/scripts/marker.sh) and absolute
# path form (/home/<user>/.claude/scripts/marker.sh) are both accepted.
# No bash wrapper, no env-var prefix, no chain operator, no redirect (except
# trailing `2>/dev/null`), no extra args after the skill name.
VALID_PATTERN="^${MARKER_SHAPE}([[:space:]]+2>/dev/null)?[[:space:]]*\$"

if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qE "$VALID_PATTERN"; then
  exit 0
fi

# Chained-commit allowance. One or more valid `marker.sh write <skill>` shapes
# joined by `&&`, followed by `git commit ...`, is the natural atomic form an
# agent types after reviews pass. Chaining marker.sh with anything other than
# `git commit` (curl, rm, redirects, ;) stays denied by falling through to the
# message below. Coordinated with require-code-review.sh and require-skill-review.sh,
# which honor the same in-chain marker-write pattern at the commit gate.
#
# Trailing content after `git commit` is constrained to characters that cannot
# form a further shell chain or redirect (`& | ; < >`). Without that constraint
# the regex would allow `marker.sh write X && git commit && curl evil.com`,
# bypassing the gate's own design intent ("no chains to anything but git commit").
# Backticks and `$` (command substitution) remain permitted; commit messages
# containing them are uncommon enough that denying would be more disruptive than
# the marginal forge-vector they represent, and substitution is itself gated
# elsewhere.
# Note: 2>/dev/null is intentionally NOT blessed here. The tail class [^&|;<>]
# already excludes '>' as a security boundary (prevents post-commit redirects like
# `git commit > /path`). A 2>/dev/null exception would require carving out of that
# class with no observed agent friction on the commit-chain form to justify it.
VALID_CHAINED_COMMIT_PATTERN='^((~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)[[:space:]]*&&[[:space:]]*)+git[[:space:]]+commit([[:space:]]+[^&|;<>]*)?$'

if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qE "$VALID_CHAINED_COMMIT_PATTERN"; then
  exit 0
fi

# Marker-chain allowance. A chain of two-or-more valid marker.sh shapes
# joined by `&&`, any op/target combination, is permitted — the chain's end
# state is identical to running each op separately, and every op is already
# individually allowlisted (the 17 shapes in permissions.allow) or harmless
# (clear-stale only evicts dead-PID bypass markers). No new capability is
# reachable through the chain that isn't already reachable by running the
# calls one at a time.
#
# NOTE: This pattern depends on the traversal guard above running first —
# that check is the sole validator of non-first segments' paths (Stage 2's
# anchor above only checks position 0 = the first segment). Do not move
# this block above the traversal guard.
VALID_MARKER_CHAIN_PATTERN="^${MARKER_SHAPE}([[:space:]]*&&[[:space:]]*${MARKER_SHAPE})+([[:space:]]+2>/dev/null)?[[:space:]]*\$"

if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qE "$VALID_MARKER_CHAIN_PATTERN"; then
  exit 0
fi

# Deny. Truncate to 80 chars to avoid echoing attacker-controlled bytes verbatim.
TRUNCATED=$(printf '%s' "$TRIMMED" | cut -c1-80)
emit_deny "marker.sh invocation denied. Command (truncated): $TRUNCATED

Valid shapes:
  ~/.claude/scripts/marker.sh write code-review
  ~/.claude/scripts/marker.sh write skill-review
  ~/.claude/scripts/marker.sh write plan-review
  ~/.claude/scripts/marker.sh write ready-for-review
  ~/.claude/scripts/marker.sh activate plan-review
  ~/.claude/scripts/marker.sh activate ready-for-review
  ~/.claude/scripts/marker.sh activate respond-pr
  ~/.claude/scripts/marker.sh activate memory-skill
  ~/.claude/scripts/marker.sh activate handoff
  ~/.claude/scripts/marker.sh deactivate plan-review
  ~/.claude/scripts/marker.sh deactivate ready-for-review
  ~/.claude/scripts/marker.sh deactivate respond-pr
  ~/.claude/scripts/marker.sh deactivate memory-skill
  ~/.claude/scripts/marker.sh deactivate handoff
  ~/.claude/scripts/marker.sh clear-stale
  ~/.claude/scripts/marker.sh clear-stale --dry-run
  ~/.claude/scripts/marker.sh resolve-session-id
  ~/.claude/scripts/marker.sh status
  ~/.claude/scripts/marker.sh check code-review

Chains of valid marker.sh operations joined by && are permitted. Chaining to
any other command (except the blessed 'git commit' tail), or using ||/;,
redirects, or extra args, is denied. Env-var prefix, bash wrapper, and
relative-path forms are not gated here — they are denied by permissions.allow."
