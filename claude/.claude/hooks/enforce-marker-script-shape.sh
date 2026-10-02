#!/bin/bash
# hook-class: gate
# tier-threat-model: cooperative, untrusted-input, irreversible
# Gate: guard review-marker and review-ledger state. Two jobs:
#   1. Deny writes that assert a decision the caller never made:
#      - Gate-releasing writes (a marker file path via Write/Edit/MultiEdit,
#        or `marker.sh write|activate` via Bash) from agent types that cannot
#        have run the review a gate demands.
#      - Any `review-ledger.sh append` via Bash from those same agent types,
#        because the row would be attributed to the parent session.
#      - Ledger-state writes (a ledger path via Write/Edit/MultiEdit or a Bash
#        redirect) from those same agent types, whether or not `.agent_id` is
#        present, and from every other subagent.
#      - A `review-ledger.sh append` carrying `--engineer-quote`, from every
#        subagent.
#      Ledger state and the engineer-quote row are barred to every subagent
#      because only the main session holds the engineer's turn and only
#      review-ledger.sh writes ledger files. A subagent is a payload with a
#      non-empty `.agent_id`; `.agent_type` alone does not identify one, since
#      the harness also sets it for a main session started with `--agent`
#      (primary source quoted in the known-gaps list below). Roster membership
#      keys on `.agent_type`, so a roster name is barred from marker and ledger
#      state with or without `.agent_id`.
#   2. Enforce strict invocation shape for ~/.claude/scripts/marker.sh.
#
# Per-fire cost is stated in the comment above the Bash write scan.
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
#     `cp`/`mv`/`install`/`ln`/`link` (last-argument form), `dd of=`, or
#     `sed -i` is caught by a dedicated scan that runs before Stage 1,
#     independent of the command mentioning `marker.sh`. Still open: `>|` (clobber-override --
#     its literal `|` gets severed from the operator by the fragment
#     splitter this scan reuses, before extraction ever sees a whole token);
#     `python3 -c "open(...).write(...)"` and here-doc bodies handed to an
#     interpreter; a `$(...)`-computed target path; shell-function/variable
#     indirection around the write utility itself; `cp`/`mv`/`install`/`ln -t
#     DIR` or `--target-directory=DIR` (destination isn't the last argument, so
#     the last-argument heuristic misses it; GNU `link` takes no such option);
#     a symlink whose own path text carries no literal `.claude` (e.g. `ln -s
#     ~/.claude/code-review-markers /tmp/x`, then `printf ... > /tmp/x/forged`)
#     — and the same for a symlink to the ledger directory, whose path carries
#     no ledger name either. This scan's fast-reject requires one of those
#     literals, unlike the Write/Edit arm's unconditional realpath resolution.
#     A symlink alias also goes unresolved past the first
#     `$MARKER_WRITE_REALPATH_BUDGET` `.claude`-mentioning candidates in one
#     command (the budget bounds per-fire cost against a many-target
#     `tee`/`cp`/`mv`/`install` invocation), or with `realpath` missing,
#     failing or timed out.
#     A dot-segment (`..`, `.`) or doubled-slash spelling is not affected by
#     that, because `_marker_shape_match` classifies a lexically normalized
#     candidate without `realpath`.
#     Still open: a bare relative spelling with no leading `/` or `./`
#     (`.claude/review-narrative-ledger/f`) is classified none without a
#     `realpath` form, since the shape patterns need a `/.claude/` segment.
#     With a `realpath` form it resolves against the hook's working directory
#     (see the `cd` residual below).
#     Still open: a CLAUDE_CONFIG_DIR with no `.claude` path segment
#     (`_marker_shape_match`'s config-dir-aware shape) —
#     both this scan's Stage-0 command-level pre-filter and its per-candidate
#     `_marker_write_candidate_mentions_claude` filter require a literal
#     `.claude` substring before a candidate ever reaches `_marker_shape_match`,
#     so a config-dir-resolved marker write with no such substring anywhere
#     in the command is never scanned at all. The Write/Edit/MultiEdit arm has
#     no such pre-filter (it always resolves its one target), so it is not
#     affected. A command naming `review-narrative-ledger` is scanned
#     regardless of `.claude`, so ledger paths are not affected either.
#     Closing this for markers means loosening a pre-filter deliberately kept
#     subprocess-free for per-fire cost — out of scope here.
#   - `deactivate` / `clear-stale` are ungated for every agent type (they
#     re-arm gates rather than release them).
#   - Marker state reached by a tool other than Bash/Write/Edit/MultiEdit
#     (none exists today) would be ungated.
#   - Both arms identify a subagent by `.agent_id`. The Claude Code hooks
#     reference (https://code.claude.com/docs/en/hooks.md, common input fields)
#     says `agent_id` is "Unique identifier for the subagent. Present only when
#     the hook fires inside a subagent call. Use this to distinguish subagent
#     hook calls from main-thread calls." and `agent_type` is "Present when the
#     session uses `--agent` or the hook fires inside a subagent." The same
#     page says "When a subagent calls a tool, tool events such as `PreToolUse`
#     and `PostToolUse` fire the same configured hooks as in the main
#     conversation, and the input carries the `agent_id` and `agent_type`
#     common input fields that identify the subagent." That sentence is what
#     the deny's completeness rests on. It does not cover a fork or a nested
#     `claude -p` session. A payload that lacks `agent_id` reads as the main
#     session, so the engineer-row and non-roster ledger-state denies fail
#     open there. That covers a subagent whose payload omits the field, a
#     nested top-level session shelled out of a Bash tool call
#     (`claude -p ...`), and a fork, whose payload is unverified. Unconfirmed
#     whether the nested session is reachable from a subagent's execution
#     context; if it is, every agent-identity-keyed hook shares it
#     (deny-reviewer-tree-mutation.sh has the same dependency), so the fix
#     belongs at the permission layer for the whole class rather than here.
#   - MARKER_WRITE_COMMAND_UNQUOTED's sed/tr strip and
#     _bash_marker_redirect_candidates's own _lib_split_fragments call both
#     check their exit status and fail closed, matching
#     deny-network-installs.sh's COMMAND_UNQUOTED_EXIT/FRAGMENTS_SPLIT_EXIT
#     pattern.
#   - A brace-expansion construct anywhere in the command (e.g. `t{ee,ee}`,
#     which bash executes as `tee tee`) defeats every literal-text scan in
#     this hook's Bash arms. The write-utility-candidate scan never matches
#     a literal `tee` against the un-expanded `t{ee,ee}` token. The
#     marker.sh Stage-1 fast-reject (a `*marker.sh*` match against raw
#     `$COMMAND` and its quote-stripped, continuation-joined copy) is defeated
#     the same way: `marker.s{h,h}` contains no literal `marker.sh` substring,
#     so the fast-reject exits before the gate-release check ever runs. The
#     Stage-2 anchor check is defeated identically. This closes no worse than
#     the eight bypasses already
#     disclosed above; the `python3 -c 'open(...).write(...)'` and
#     here-doc-body bypasses are easier for an agent to produce unprompted
#     than deliberate brace-splitting. This repo has no `permissions.allow`
#     entry for `tee`, `cp`, `mv`, `install`, `dd`, `sed`, or `python3`. In a
#     session running under normal permission mode, an unrecognized
#     invocation of one of those falls through to the harness's
#     ask-by-default prompt. A session running under `auto`,
#     `bypassPermissions`, or `dontAsk` mode has no such backstop. Gated
#     agents including `code-writer` routinely run under those modes. For
#     that mode, this gap is a live, unmitigated path to forging a
#     `/code-review` or `/plan-review` completion marker.
#   - `_lib_realpath_m`'s fallback loop wraps its `test -e`/`test -L` calls in
#     `_lib_capped` (external subprocess + timeout), which widens — in degree,
#     not in kind — the check-then-use race already inherent between this
#     hook's `_marker_shape_match` resolution and a target's next use.
#   - The review-ledger arms have these residuals:
#     - The Bash text arms read the command after quote removal and
#       line-continuation joining, with a leading `~`, `$HOME` or
#       `$CLAUDE_CONFIG_DIR` expanded in a write target. Anything the shell or
#       filesystem resolves later is a residual, pinned by an allowed-residual
#       test.
#     - The leading `$CLAUDE_CONFIG_DIR` expansion is effective for ledger
#       targets only. The per-candidate pre-filter drops
#       `$CLAUDE_CONFIG_DIR/<kind>-markers/...` before the expansion runs when
#       it carries neither `.claude` nor `review-narrative-ledger`.
#     - The quoted-pattern join `${var//"$BACKSLASH_NEWLINE"/...}` is unverified
#       on bash 3.2, and no test exercises that version. If 3.2 ignores the
#       quoted pattern, both joins become no-ops there.
#     - The `append` Bash arm is text-only, with no path arm, no Stage 2
#       backstop and no `permissions.allow` entry.
#     - Variable and function indirection, brace expansion, `$'\x..'`
#       escapes, and sourcing `_review-ledger-lib.sh` are not matched.
#     - A script name, op or flag supplied at run time is not matched: command
#       substitution, `xargs` stdin, or a glob in the script name.
#     - A case-varied script name (`Review-Ledger.sh`), which runs on a
#       case-insensitive volume, is not matched by either Bash text arm.
#       marker.sh's Stage 1 shares this.
#     - The Bash write scan runs only when the command carries the literal
#       `.claude` or `review-narrative-ledger`. Only a leading `~`, `$HOME` or
#       `$CLAUDE_CONFIG_DIR` is expanded in a write target; any other variable,
#       glob or `$(...)` is not.
#     - A relative write target after `cd` into the ledger directory is not
#       resolved against the `cd`.
#     - `rm` and `truncate` on a ledger or marker file are not scanned as
#       writes.
#     - `mv` with a ledger or marker file as its source is not scanned as a
#       write.
#     - A `>&` redirect (`echo x >& PATH`) yields the glued target `&`, so the
#       path word after it is never a candidate.
#     - A trailing redirect after a `cp`/`mv`/`install`/`ln`/`link` destination
#       (`cp SRC DEST 2>/dev/null`) becomes the last argument, so DEST is never
#       a candidate.
#     - A `cp`/`mv`/`install`/`ln`/`link` destination that is a symlink to a
#       ledger or marker directory is not classified as that directory.
#       `realpath -m` drops the appended trailing slash, and the lexical form
#       follows no symlink. A bare destination that names the directory itself,
#       including a doubled-slash or dot-segment spelling of it, is classified.
#     - The Bash write scan resolves a symlink only when the candidate text
#       names `.claude` or the ledger directory and realpath budget remains,
#       and never follows a hard link. The Write/Edit arm resolves a symlink
#       but not a hard link. Two routes follow:
#       - A write through an alias created outside the state directory.
#       - `ln -f -t <state dir> <same-filesystem file>`, which the `-t` residual
#         above misses. review-ledger.sh accepts the result, because it refuses
#         a symlink or non-regular file at a ledger or lock path and a hard link
#         is a regular file.
#     - Accepted over-emission: `cp -t DIR <state dir>`, `ln -t DIR <state dir>`
#       and `install -d <state dir>` are denied for a subagent, because the
#       last argument is classified as a destination directory. None has a
#       legitimate subagent use.
#     - A write utility behind `bash -c` or a compound keyword (`then cp ...`)
#       is not matched, because the command word is the wrapper or the keyword.
#       A redirect inside the same text is still matched.
#     - A case-varied `TEE` command word passes the case-folding command-word
#       check, then fails the `tee` argument walk, which compares
#       case-sensitively.
#     - The engineer-row deny depends on the script's own quote requirement:
#       `_review_ledger_validate_flags` rejects `--decided-by engineer`
#       without `--engineer-quote`.
#     - A `--decided-by plan-architect` row, and a DEFER row, from a
#       non-roster agent stay allowed. That is a decision, not an omission:
#       `general-purpose` is the delegated review orchestrator and logs rows
#       the way the dispatcher would, and plan-architect holds no Bash tool,
#       so only a dispatcher can log its decision.
#     - Unverified whether `.agent_id` is present inside a fork. If it is
#       absent, a fork reads as the main session and is not denied an
#       engineer-row append or a ledger-state write, unless its `.agent_type`
#       is in the roster.
#
# WARNING: Do NOT remove the internal marker.sh check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands. The internal grep is the actual
# gate. The "if" field is a hint only.
#
# Commands that start directly with the marker.sh path (~/ or absolute) must
# match one of the 22 single-command shapes, the marker.sh write chain to a
# commit-concluding git command, or a chain of two-or-more valid marker.sh
# shapes joined by `&&` (any op/target combination) — equivalent to running each op separately,
# since every marker operation is independently allowlisted or harmless. No
# redirects (except trailing `2>/dev/null`), no extra args. Wrapped forms
# (env-var prefix, bash wrapper, relative path, subshell) are not gated here —
# they fast-exit at Stage 2 and are denied by
# the permissions.allow layer, which does not list their wrapper executables.
# Removing the permissions.allow gate without updating this hook would leave
# those forms ungated.
set -uo pipefail

DENY_GATE_LABEL="marker-script-shape"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  # False positive: shellcheck's static pass doesn't model this stub-then-
  # override redefinition, which resolves correctly at call time (see
  # _lib.sh's _lib_emit_deny comment). Considered moving the definition
  # after the call instead, but that defeats the bootstrap's job of
  # covering the case where sourcing _lib.sh itself fails.
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

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
REPORT_DENIAL_UPWARD_GUIDANCE="Report the denial to the dispatching session instead: name the gate that blocked you, the command or path it blocked, and what you had completed. The dispatching session runs the review skill (or delegates it to a general-purpose subagent, which does carry Skill) and re-dispatches you."

GATE_RELEASE_DENIAL_GUIDANCE="Releasing a gate requires having run the review that the gate demands, and this agent type could not have run it — either it carries no Skill tool and cannot invoke a review skill at all, or it is dispatched read-only by mandate. Either way a marker it writes would assert a review that never happened. Marker writes are attributed to the parent session, so this would release the gate for the whole session, not just this subagent.

$REPORT_DENIAL_UPWARD_GUIDANCE

Matching a hash you computed yourself is not authorization — an equal hash shows the state is unchanged, not that anyone reviewed it."

# Ledger rows resolve their session the way marker writes do, so a subagent's
# row lands in the parent session's ledger.
LEDGER_UPWARD_GUIDANCE="Report the denial to the dispatching session instead: name the command or path it blocked and the row you meant to log. The dispatching session decides whether to log it."

# Appended to the roster's `append` denial, the only ledger denial whose match
# is on the op word. The engineer-row denial carries its own wording, and the
# path-keyed ledger denials match a resolved path.
LEDGER_COMMAND_TEXT_NOTE="This matched on command text. The Grep and Read tools are unaffected, and a search that leaves out the op word (for example \`grep -rn review-ledger.sh\`) is not blocked."

# _marker_shape_match TARGET_PATH [ALLOW_REALPATH=1]
# True (exit 0) iff TARGET_PATH matches the marker-directory SHAPE or the
# review-ledger-directory shape (`review-narrative-ledger/`), tested via
# the raw tilde-expansion, its lexical normalization and its `_lib_realpath_m`
# normalization, in that order, so both call sites (Write/Edit/MultiEdit's
# single target below, the Bash redirect/utility arm's several extracted
# targets further down) share one pattern that cannot drift between them.
# Exit 1: no match. Exit 2: the Claude Code
# config directory could not be resolved, so a config-dir-relative marker
# alias could not be ruled out — callers must deny, not skip, on exit 2.
# Shape test only: no agent-type read, no deny decision — callers decide what
# a match or a resolution failure means. On a match it sets
# MARKER_SHAPE_MATCH_KIND to `marker` or `ledger`, so a caller can word its
# denial for the state that matched. The name stays `_marker_shape_match` for
# its existing callers.
#
# Shape-anchored, not $HOME-prefixed: stow-fold makes the same marker also
# reachable at <repo>/claude/.claude/<kind>-markers/, which has no $HOME
# segment, and a `..` segment doesn't carry a literal $HOME/.claude/ prefix
# until normalized. A second, independent shape covers CLAUDE_CONFIG_DIR
# values with no `.claude` segment at all (e.g. ~/.config/claude-accounts/
# <account>), which the $HOME-relative shape above cannot see.
# The raw and lexical candidates are always tested, since `_lib_realpath_m` can
# return empty under `_lib_capped`'s timeout on a stalled $HOME mount. The
# lexical form (`_lib_normalize_path_lexically`) runs no exec'd command, so ALLOW_REALPATH
# bounds only symlink resolution, and a `//`, `/./` or `seg/../` spelling is
# classified whether or not a realpath form exists. The ledger globs have no
# wildcard between the anchor and the directory name, so only a normalized
# spelling matches them.
# `realpath` is still required to catch a symlink whose own path carries no
# marker-shaped segment but resolves into the markers directory — the same
# reasoning applies to the config dir itself, so it is realpath'd too. The
# config dir is also normalized lexically, so an anchor spelled with `//` or
# `..` still matches a normalized candidate.
# Only a leading `~`, `$HOME` or `$CLAUDE_CONFIG_DIR` in TARGET_PATH is
# expanded; any other variable, glob or `$(...)` stays literal.
# Over-matching is safe: a false marker or ledger match only denies a roster
# agent, which has no legitimate direct write to either directory, or a
# subagent, which has none to the ledger directory.
#
# The config-dir branch runs only when CLAUDE_CONFIG_DIR is actually set:
# _lib_config_dir()'s fallback (unset CLAUDE_CONFIG_DIR) resolves to exactly
# $HOME/.claude, a strict subset of the $HOME-relative shape test below —
# a candidate that would match the config-dir-anchored pattern in that
# default case necessarily already matches the $HOME-relative one, so
# running it would only add a redundant `_lib_config_dir`/`_lib_realpath_m`
# call for the overwhelming majority of installations that never set
# CLAUDE_CONFIG_DIR. When it IS set, resolution and its realpath follow the
# same ALLOW_REALPATH gating as the $HOME-relative candidate: the raw and
# lexical resolved values are always tested, only the realpath normalization
# is budget-gated, so a budget-exhausted candidate degrades the same way the
# $HOME-relative shape does rather than losing config-dir coverage entirely.
# A resolution failure denies (return 2) unconditionally once CLAUDE_CONFIG_DIR
# is set, for the same reason the Write/Edit/MultiEdit arm denies
# unconditionally: an unresolvable config dir means no candidate here can be
# verified as NOT a review-marker path, independent of the realpath budget.
_marker_shape_match() {
  local target_path="$1" allow_realpath="${2:-1}"
  local expanded lexical normalized candidate matched=1
  MARKER_SHAPE_MATCH_KIND=""
  local config_dir_resolved="" config_dir_lexical="" config_dir_realpath=""
  expanded="${target_path/#\~/$HOME}"
  if [ -n "${CLAUDE_CONFIG_DIR:-}" ]; then
    if ! config_dir_resolved=$(_lib_config_dir 2>/dev/null); then
      return 2
    fi
    _lib_normalize_path_lexically "$config_dir_resolved"
    # The normalizer keeps a trailing slash, and the anchor patterns append `/`.
    # A bare `/` config dir strips to empty, which the anchor loop skips.
    config_dir_lexical="${_LIB_NORMALIZED_PATH%/}"
    if [ "$allow_realpath" = "1" ]; then
      config_dir_realpath=$(_lib_realpath_m "$config_dir_resolved" 2>/dev/null)
    fi
  fi
  # The shell expands a leading $HOME or $CLAUDE_CONFIG_DIR before the write
  # lands. An unset CLAUDE_CONFIG_DIR expands to empty, so it is left alone
  # rather than rewritten to the default config dir.
  local variable_prefix
  for variable_prefix in "\$HOME/" "\${HOME}/"; do
    if [ -n "${HOME:-}" ] && [ "${expanded:0:${#variable_prefix}}" = "$variable_prefix" ]; then
      expanded="$HOME/${expanded:${#variable_prefix}}"
      break
    fi
  done
  for variable_prefix in "\$CLAUDE_CONFIG_DIR/" "\${CLAUDE_CONFIG_DIR}/"; do
    if [ -n "$config_dir_resolved" ] && [ "${expanded:0:${#variable_prefix}}" = "$variable_prefix" ]; then
      expanded="$config_dir_resolved/${expanded:${#variable_prefix}}"
      break
    fi
  done
  _lib_normalize_path_lexically "$expanded"
  lexical="$_LIB_NORMALIZED_PATH"
  if [ "$allow_realpath" = "1" ]; then
    normalized=$(_lib_realpath_m "$expanded" 2>/dev/null)
  else
    normalized=""
  fi
  local candidate_kind config_dir_anchor
  for candidate in "$expanded" "$lexical" "$normalized"; do
    [ -n "$candidate" ] || continue
    candidate_kind=""
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
    # Ledger files (<config-dir>/review-narrative-ledger/*) carry decision rows
    # the parent session would be credited with, including their `.lock` files.
    # A ledger shape on any candidate form or pattern list wins over a marker
    # shape, so `<kind>-markers/../review-narrative-ledger/<file>` is
    # ledger-kind.
    case "$candidate" in
      */.claude/review-narrative-ledger/*) candidate_kind=ledger ;;
      */.claude/*-markers/*|*/.claude/.*-active.d/*) candidate_kind=marker ;;
    esac
    for config_dir_anchor in "$config_dir_resolved" "$config_dir_lexical" "$config_dir_realpath"; do
      [ "$candidate_kind" != ledger ] || break
      [ -n "$config_dir_anchor" ] || continue
      case "$candidate" in
        "$config_dir_anchor"/review-narrative-ledger/*) candidate_kind=ledger ;;
        "$config_dir_anchor"/*-markers/*|"$config_dir_anchor"/.*-active.d/*) candidate_kind=marker ;;
      esac
    done
    shopt -u nocasematch
    [ -n "$candidate_kind" ] || continue
    matched=0
    MARKER_SHAPE_MATCH_KIND="$candidate_kind"
    [ "$candidate_kind" = ledger ] && break
  done
  return "$matched"
}

# _agent_barred_from_matched_state
# Exit 0 iff the caller may not write the state the last _marker_shape_match
# call matched. A roster agent is barred from any match, marker or ledger,
# whether or not AGENT_ID is present, since the match reports one kind and a
# path can carry both (`<kind>-markers/../review-narrative-ledger/<file>`).
# Any other subagent (non-empty AGENT_ID) is barred from ledger state only,
# since only the main session holds the engineer's turn and review-ledger.sh
# is the only legitimate writer. A full-tool-set agent may have run a review,
# so marker state stays open to it, and a named main session (AGENT_TYPE with
# no AGENT_ID) stays open to ledger state.
_agent_barred_from_matched_state() {
  _lib_is_no_gate_release_agent "$AGENT_TYPE" && return 0
  [ "${MARKER_SHAPE_MATCH_KIND:-}" = ledger ] && [ -n "$AGENT_ID" ]
}

# _shape_match_denial_reason DISPLAY_PATH
# Prints the deny reason for the state kind the last _marker_shape_match call
# matched. Runs in a command substitution, so it only builds text and cannot
# deny; the caller passes the result to emit_deny.
_shape_match_denial_reason() {
  local display_path="$1"
  if [ "${MARKER_SHAPE_MATCH_KIND:-}" = ledger ]; then
    printf "Ledger write — the '%s' agent cannot write review-ledger state at '%s'. Only review-ledger.sh writes ledger files, and its rows are credited to the parent session as decisions, so a file written by hand would assert a decision the parent session never made.\n\n%s" \
      "$AGENT_DENIAL_LABEL" "$display_path" "$LEDGER_UPWARD_GUIDANCE"
  else
    printf "Marker write — the '%s' agent cannot release a review gate by writing '%s'.\n\n%s" \
      "$AGENT_DENIAL_LABEL" "$display_path" "$GATE_RELEASE_DENIAL_GUIDANCE"
  fi
}

# _config_dir_unresolvable_reason DISPLAY_PATH
# Prints the deny reason for a _marker_shape_match exit 2. Runs in a command
# substitution, so it only builds text and cannot deny.
_config_dir_unresolvable_reason() {
  printf "Marker or ledger write — could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path) to verify '%s' is not a review-marker or review-ledger path." "$1"
}

# Defense-in-depth: filter on tool name here rather than relying on the
# settings.json matchers alone. Anything that is neither a file-write tool nor
# Bash cannot reach marker state.
#
# Both arms key on .agent_id and .agent_type; neither pays a per-fire
# subprocess for them, since _lib_parse_tool_input_or_deny already populates
# AGENT_ID and AGENT_TYPE (and, for this arm, FILE_PATH) from the single shared
# parse every hook invocation already pays for.
# AGENT_DENIAL_LABEL names the caller in deny text; a subagent with no
# agent_type reads as 'unnamed'.
AGENT_DENIAL_LABEL="${AGENT_TYPE:-unnamed}"
case "$TOOL_NAME" in
  Write|Edit|MultiEdit)
    # Path-based arm. Every marker lives under a known directory, so the
    # decision is "is this agent writing marker state?" — a question the
    # resolved path answers directly, with no command text to outsmart.
    TARGET_PATH="$FILE_PATH"

    # The main session writes any state. Which subagents may depends on the
    # matched kind, so the roster test follows the shape match. A named agent
    # without an agent_id is a main session started with `--agent`; it still
    # reaches the shape match so a roster name stays barred from marker state.
    [ -n "$AGENT_ID" ] || [ -n "$AGENT_TYPE" ] || exit 0
    [ -n "$TARGET_PATH" ] || exit 0

    # Shape-tested via _marker_shape_match (defined above; shared with the
    # Bash redirect/utility arm below, including its config-dir-resolution-
    # failure exit code) — see its comment for the pattern rationale.
    _marker_shape_match "$TARGET_PATH"
    case "$?" in
      0)
        if _agent_barred_from_matched_state; then
          emit_deny "$(_shape_match_denial_reason "$TARGET_PATH")"
        fi
        ;;
      2)
        emit_deny "$(_config_dir_unresolvable_reason "$TARGET_PATH")"
        ;;
    esac
    exit 0
    ;;
  Bash) ;;
  *) exit 0 ;;
esac

# _fragment_may_invoke_tool FRAGMENT TOOL
# Same answer as _lib_fragment_invokes_tool. A pure-bash substring test runs
# first, so a fragment that never names TOOL as a word or path component skips
# that function's command-word subshell. The test folds case, as that function
# does, so `CP` still reaches it.
_fragment_may_invoke_tool() {
  local fragment="$1" tool="$2" may=1
  shopt -s nocasematch
  case "$fragment" in
    "$tool" | "$tool"[[:space:]]* | *[[:space:]/]"$tool" | *[[:space:]/]"$tool"[[:space:]]*) may=0 ;;
  esac
  shopt -u nocasematch
  [ "$may" -eq 0 ] && _lib_fragment_invokes_tool "$fragment" "$tool"
}

# _bash_marker_fragment_candidates FRAGMENT
# Emits, one per line, every write-target word in FRAGMENT worth
# shape-testing: `>`/`>>` operands (bare or glued, fd-prefixed), `tee`
# arguments, `cp`/`mv`/`install`/`ln`/`link` last arguments (each also with a
# trailing `/` when it has none, so a bare destination directory is classified
# like a path inside it), `dd of=`
# glued arguments, and `sed -i` last arguments. Over-emission is safe — each
# candidate is independently shape-tested by _marker_shape_match.
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

  if _fragment_may_invoke_tool "$fragment" tee; then
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

  if _fragment_may_invoke_tool "$fragment" cp \
    || _fragment_may_invoke_tool "$fragment" mv \
    || _fragment_may_invoke_tool "$fragment" install \
    || _fragment_may_invoke_tool "$fragment" ln \
    || _fragment_may_invoke_tool "$fragment" link; then
    printf '%s\n' "${Words[$((n - 1))]}"
    # A destination that is an existing directory receives the file inside it.
    # The shape globs end in `/*`, so a destination with no trailing slash
    # needs one appended to match; the `*` then matches the empty remainder.
    case "${Words[$((n - 1))]}" in
      */) ;;
      *) printf '%s/\n' "${Words[$((n - 1))]}" ;;
    esac
  fi

  if _fragment_may_invoke_tool "$fragment" dd; then
    for word in "${Words[@]}"; do
      case "$word" in
        # Substring offset, not a `#`-prefix strip: the latter's literal
        # "of=" reads as a Slack-channel-shaped reference to this repo's own
        # redaction detector. offset 3 skips exactly "of=", matched above.
        of=*) printf '%s\n' "${word:3}" ;;
      esac
    done
  fi

  if _fragment_may_invoke_tool "$fragment" sed; then
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

# _text_names_ledger_directory TEXT
# Exit 0 iff TEXT contains the ledger directory name, the LEDGER_DIR segment
# review-ledger.sh builds under the config dir. Pure bash, and
# case-insensitive for the same reason _marker_shape_match is.
_text_names_ledger_directory() {
  local names=1
  shopt -s nocasematch
  case "$1" in
    *review-narrative-ledger*) names=0 ;;
  esac
  shopt -u nocasematch
  return "$names"
}

# _text_may_name_state_directory TEXT
# Exit 0 iff TEXT carries a literal `.claude` or the ledger directory name,
# the two substrings every marker or ledger path alias contains. The shared
# pure-bash pre-filter for the Bash write scan, both on the whole command and
# on each extracted candidate.
_text_may_name_state_directory() {
  _marker_write_candidate_mentions_claude "$1" || _text_names_ledger_directory "$1"
}

# The pair the shell deletes when a command line ends in a backslash.
BACKSLASH_NEWLINE=$'\\\n'

# _script_op_scan COMMAND COMMAND_UNQUOTED_JOINED SCRIPT_REGEX TOOL OP...
# Exit 0: COMMAND invokes TOOL with one of the OPs. Exit 1: it does not.
# Exit 2: a needed fork failed (sed/tr missing, killed, or errored), so the
# answer is unknown.
# COMMAND_UNQUOTED_JOINED is _lib_strip_shell_quotes of COMMAND with each
# backslash-newline pair deleted, which the caller has already computed and
# checked.
# Two detectors, unconditionally OR'd. Each covers a shape the other misses:
#   - Raw-text regex SCRIPT_REGEX, whitespace, then an OP, on COMMAND (a
#     backslash-newline counts as whitespace) and on COMMAND_UNQUOTED_JOINED.
#     It sees inside `bash -c "..."` and `eval "..."` wrappers. The
#     quote-stripped pass alone sees a quote split between the script and the
#     OP.
#   - _lib_command_invokes_tool_subcmd, once per OP because it matches its
#     SUBCMD sequence positionally. Only it reads a flag placed between the
#     script and the OP, and only it can return status 2. It gets the raw
#     COMMAND only, since _lib_strip_shell_quotes is not idempotent.
# The raw-text match is a bash `=~`, not `printf | grep -q`: under pipefail,
# grep exits at the first matching line while printf is still writing a large
# command, and the pipeline's 141 would read as no match.
# SCRIPT_REGEX is an ERE fragment each call site passes literally for its own
# TOOL, never derived from it.
# Never denies: it runs in the caller's top-level shell, and each call site
# emits its own denial. A match from any detector or op beats an indeterminate
# op. Sets no IFS.
_script_op_scan() {
  local command="$1" command_unquoted_joined="$2" script_regex="$3" tool="$4"
  shift 4
  local op ops_alternation="" op_status indeterminate=false
  local command_joined=${command//"$BACKSLASH_NEWLINE"/ }
  for op in "$@"; do
    ops_alternation="${ops_alternation:+$ops_alternation|}$op"
  done
  local detector_regex="${script_regex}[[:space:]]+(${ops_alternation})"
  if [[ "$command_joined" =~ $detector_regex ]] || [[ "$command_unquoted_joined" =~ $detector_regex ]]; then
    return 0
  fi
  for op in "$@"; do
    _lib_command_invokes_tool_subcmd "$command" "$tool" "$op"
    op_status=$?
    if [ "$op_status" -eq 0 ]; then
      return 0
    elif [ "$op_status" -ne 1 ]; then
      indeterminate=true
    fi
  done
  if $indeterminate; then
    return 2
  fi
  return 1
}

# Bash-tool write to a marker or ledger path via a redirect or write utility
# that never mentions `marker.sh` — closes the class of bypass Stage 1's
# substring gate below would otherwise fast-exit as an allow. Runs first for
# that reason.
# Fast-reject mirrors Stage 1's own cheap-prefilter discipline: every alias
# of a marker path contains the literal '.claude', and every alias of a ledger
# path contains the ledger directory name, which is also what keeps a config
# dir with no `.claude` segment in scope. Case-insensitive for the same reason
# _marker_shape_match's case pattern is: macOS's default APFS volume is
# case-insensitive. Runs against the quote-stripped command, not raw $COMMAND,
# so `~/.cla''ude/...` (which the shell collapses to `.claude` at execution
# time) can't skip this fast-reject by never containing a contiguous `.claude`
# substring in its raw text.
#
# Per-fire cost on every Bash call whether or not it is marker-shaped, counted
# in exec'd external commands except where a figure says processes. The scan
# cannot move after Stage 1, since it exists to catch a command that never
# reaches Stage 1's marker.sh substring check. The quote-strip is two externals (sed,
# tr), and about seven processes once its `$(...)` substitutions and the
# builtin `printf` on the left of each pipe are counted (measured on bash 5.2
# by counting `clone` calls under strace). The backslash-newline join, the
# pre-filter and Stage 1's fast-reject are pure bash. Splitting the text into
# fragments is two seds, once, and about four processes. A join that changes
# the text makes the scan read the unjoined and the joined copy, which doubles
# the fragment count.
# Per fragment, `_fragment_may_invoke_tool` rejects with a `case` match, so a
# fragment that names none of tee, cp, mv, install, ln, link, dd and sed as a
# word or path component costs none. Each of those utilities a fragment does
# name costs one `_lib_fragment_command_word` command substitution, at most
# eight per fragment.
# Per candidate, the pre-filter rejects with a `case` match and costs none. A
# candidate that reaches _marker_shape_match's realpath resolution costs one
# `_lib_realpath_m` call in the default configuration (CLAUDE_CONFIG_DIR
# unset), or two (target path, then config dir) once CLAUDE_CONFIG_DIR is
# set -- _marker_shape_match skips the config-dir branch entirely in the
# default case, since it would be redundant with the $HOME-relative shape
# test. MARKER_WRITE_REALPATH_BUDGET below bounds how many candidates in one
# fire pay that cost, capping worst-case added latency at roughly
# budget * (1 or 2 realpath calls, depending on CLAUDE_CONFIG_DIR). The lexical
# form _marker_shape_match always tests costs no exec'd command. Fragment
# count is not capped: it scales with the command's line count, at the
# per-fragment cost above. Absolute per-call latency is too load-dependent on
# a shared machine to state as a fixed ms figure here.
# The ledger Bash arm costs none on the common path (no subagent, or no
# `review-ledger.sh` in the command), since both of its predicates gate their
# detectors behind pure-bash `case` matches. The Write/Edit/MultiEdit arm
# resolves its target whenever `.agent_id` or `.agent_type` is non-empty, not
# only for the roster, because a ledger path denies every subagent.
# A future edit that removes a pre-filter, raises the budget, or adds a
# per-fragment or per-candidate external command should re-derive this
# accounting.
MARKER_WRITE_COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
MARKER_WRITE_COMMAND_UNQUOTED_EXIT=$?
if [ "$MARKER_WRITE_COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "could not quote-strip the command text (exit ${MARKER_WRITE_COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach marker state."
  exit 0
fi
# The shell deletes a backslash-newline pair, so a name split across it by one
# still names the ledger directory. The joined copy is scanned in addition to
# the unjoined one: deleting the pair before the fragment split could merge a
# line ending in an escaped backslash into the next fragment.
MARKER_WRITE_COMMAND_UNQUOTED_JOINED=${MARKER_WRITE_COMMAND_UNQUOTED//"$BACKSLASH_NEWLINE"/}
MARKER_WRITE_SCAN_TEXT="$MARKER_WRITE_COMMAND_UNQUOTED"
if [ "$MARKER_WRITE_COMMAND_UNQUOTED_JOINED" != "$MARKER_WRITE_COMMAND_UNQUOTED" ]; then
  MARKER_WRITE_SCAN_TEXT="$MARKER_WRITE_COMMAND_UNQUOTED"$'\n'"$MARKER_WRITE_COMMAND_UNQUOTED_JOINED"
fi
if _text_may_name_state_directory "$MARKER_WRITE_SCAN_TEXT"; then
  MARKER_WRITE_REDIRECT_CANDIDATES=$(_bash_marker_redirect_candidates "$MARKER_WRITE_SCAN_TEXT")
  MARKER_WRITE_REDIRECT_CANDIDATES_EXIT=$?
  if [ "$MARKER_WRITE_REDIRECT_CANDIDATES_EXIT" -ne 0 ]; then
    emit_deny "could not split the command into fragments (exit ${MARKER_WRITE_REDIRECT_CANDIDATES_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach marker state."
    exit 0
  fi
  MARKER_WRITE_REALPATH_BUDGET=10
  while IFS= read -r MARKER_WRITE_CANDIDATE; do
    [ -n "$MARKER_WRITE_CANDIDATE" ] || continue
    _text_may_name_state_directory "$MARKER_WRITE_CANDIDATE" || continue
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
      emit_deny "$(_config_dir_unresolvable_reason "$MARKER_WRITE_CANDIDATE_TRUNCATED")"
      exit 0
    fi
    [ "$MARKER_WRITE_SHAPE_STATUS" -eq 0 ] || continue
    # AGENT_ID and AGENT_TYPE are already populated by
    # _lib_parse_tool_input_or_deny's shared parse, at no added per-fire cost.
    if _agent_barred_from_matched_state; then
      MARKER_WRITE_CANDIDATE_TRUNCATED=$(printf '%s' "$MARKER_WRITE_CANDIDATE" | cut -c1-80)
      emit_deny "$(_shape_match_denial_reason "$MARKER_WRITE_CANDIDATE_TRUNCATED")"
      exit 0
    fi
  # Here-string over the already-captured MARKER_WRITE_REDIRECT_CANDIDATES,
  # not a nested command substitution: the split's exit status is checked
  # above, before this loop starts, matching
  # _bash_marker_redirect_candidates's own inner loop.
  done <<< "$MARKER_WRITE_REDIRECT_CANDIDATES"
fi

# Review-ledger arm of the authority check. It sits before Stage 1, whose
# `marker.sh` fast-reject exits on every ledger command, and never exits on
# a non-match, so a combined command still reaches the marker arm below.
# Its cost is stated with the Bash write scan's. _script_op_scan takes the raw
# $COMMAND for the command-word detector, since _lib_strip_shell_quotes is not
# idempotent, and the quote-stripped, backslash-newline-joined command for its
# raw-text detector.
#   - A roster agent is denied any `append`.
#   - Any other subagent (non-empty `AGENT_ID`) is denied an `append` carrying
#     `--engineer-quote`, tested before the detectors run. That one token marks
#     every engineer-authority row, since the script rejects an engineer row
#     without it. The tested text includes a quote-split `--engineer-''quote`
#     and one split by a backslash-newline.
# `show`, `render` and `clear-stale` stay ungated.
if [ -n "$AGENT_ID" ] || [ -n "$AGENT_TYPE" ]; then
  LEDGER_SCRIPT_MENTIONED=false
  case "$COMMAND" in *review-ledger.sh*) LEDGER_SCRIPT_MENTIONED=true ;; esac
  case "$MARKER_WRITE_COMMAND_UNQUOTED_JOINED" in *review-ledger.sh*) LEDGER_SCRIPT_MENTIONED=true ;; esac
  if $LEDGER_SCRIPT_MENTIONED; then
    LEDGER_APPEND_DENIAL_KIND=""
    if _lib_is_no_gate_release_agent "$AGENT_TYPE"; then
      LEDGER_APPEND_DENIAL_KIND=roster
    elif [ -n "$AGENT_ID" ]; then
      case "$COMMAND" in *--engineer-quote*) LEDGER_APPEND_DENIAL_KIND=engineer ;; esac
      case "$MARKER_WRITE_COMMAND_UNQUOTED_JOINED" in *--engineer-quote*) LEDGER_APPEND_DENIAL_KIND=engineer ;; esac
    fi
    if [ -n "$LEDGER_APPEND_DENIAL_KIND" ]; then
      _script_op_scan "$COMMAND" "$MARKER_WRITE_COMMAND_UNQUOTED_JOINED" 'review-ledger\.sh' review-ledger.sh append
      LEDGER_APPEND_SCAN_STATUS=$?
      if [ "$LEDGER_APPEND_SCAN_STATUS" -eq 0 ]; then
        if [ "$LEDGER_APPEND_DENIAL_KIND" = roster ]; then
          emit_deny "Ledger write — the '$AGENT_DENIAL_LABEL' agent cannot append review-ledger rows, and the row would be attributed to the parent session as a decision it never made. $LEDGER_COMMAND_TEXT_NOTE

$LEDGER_UPWARD_GUIDANCE"
        else
          emit_deny "Ledger write — the '$AGENT_DENIAL_LABEL' agent cannot log an engineer decision: only the main session holds the engineer's turn, and the quote is published in the PR body under the engineer's name. Report the engineer's answer and the planned row in your return, and the main session logs it. This matched on the text \`--engineer-quote\` anywhere in the command, including inside the finding or rationale text, so rewording that text to leave out the literal flag avoids a false match. The Grep and Read tools are unaffected."
        fi
        exit 0
      fi
      if [ "$LEDGER_APPEND_SCAN_STATUS" -ne 1 ]; then
        emit_deny "could not determine whether this command invokes review-ledger.sh append (sed/tr may be missing, killed, or errored) — failing closed per this gate's documented fail-closed posture rather than letting an unscanned command log a review-ledger row for a decision this agent did not make."
        exit 0
      fi
    fi
  fi
fi

# Stage 1: cheap substring fast-reject — most Bash calls have no marker mention.
# Pure bash: under pipefail, `printf | grep -qF` reads a large multi-line
# command that mentions marker.sh as no match, and the `|| exit 0` would
# allow it. The quote-stripped, backslash-newline-joined text is tested too, so
# `mark""er.sh` and `marker.\<newline>sh` reach the gate-release check below.
# The raw mention is kept as its own flag: the checks after the gate-release
# check read raw text, and the quote-stripped copy also reads a regex-escaped
# `marker\.sh` as a mention.
MARKER_SCRIPT_MENTIONED=false
MARKER_SCRIPT_MENTIONED_IN_RAW_TEXT=false
case "$COMMAND" in *marker.sh*) MARKER_SCRIPT_MENTIONED=true; MARKER_SCRIPT_MENTIONED_IN_RAW_TEXT=true ;; esac
case "$MARKER_WRITE_COMMAND_UNQUOTED_JOINED" in *marker.sh*) MARKER_SCRIPT_MENTIONED=true ;; esac
$MARKER_SCRIPT_MENTIONED || exit 0

# Bash arm of the gate-release authority check. Placed immediately after
# Stage 1 and BEFORE Stage 2, deliberately: Stage 2 fast-exits wrapped forms
# (bash -c, env-var prefix, relative path) and leaves them to
# permissions.allow, so a check placed after it would inherit that hole.
# _script_op_scan holds the two detectors, their OR, and the status-2
# contract. Status 2 (could not determine, e.g. sed/tr missing) denies,
# matching this hook's fail-closed posture rather than silently falling
# through as "no match."
#
# SCOPE LIMIT, stated rather than implied: both detectors match command TEXT,
# so shell-level indirection that assigns the path to a variable and invokes
# through it, or wraps the call in a shell function, is not matched here —
# the same carve-out Stage 2 already documents for wrapped forms. Those
# forms are not pre-approved in permissions.allow either, so they surface as
# a permission prompt rather than a silent allow. The path-based Write/Edit
# arm above is what makes the overall property hold; do not read this arm
# as a complete boundary on its own.
#
# test_marker_script.py's TestMarkerScriptArgumentGrammarIsPositional is the
# regression guard for the positional-argument-grammar invariant this arm's
# command-word detector depends on.
#
# Accepted false-deny: a review-only agent grepping for the literal string
# `marker.sh write` while reviewing this repo is denied, and so is the
# regex-escaped spelling `marker\.sh write`, which the quote-stripped scan
# reads as the literal. Matching the op keyword rather than the bare tool name
# keeps plain `grep -rn marker.sh` available, which is the common reviewer
# action.
#
# `deactivate` and `clear-stale` are not gated — they re-arm gates rather than
# releasing them. That is directionally safe but not free: a mandate-scoped
# subagent calling `deactivate` clears the PARENT session's active-bypass
# marker (same ancestor walk) and could disrupt a review running outside its
# own turn. Narrow enough to accept; noted so the omission reads as a decision.
#
# AGENT_TYPE is already populated by _lib_parse_tool_input_or_deny's shared
# parse, at no added per-fire cost.
if _lib_is_no_gate_release_agent "$AGENT_TYPE"; then
  _script_op_scan "$COMMAND" "$MARKER_WRITE_COMMAND_UNQUOTED_JOINED" 'marker\.sh' marker.sh write activate
  MARKER_GATE_SCAN_STATUS=$?
  if [ "$MARKER_GATE_SCAN_STATUS" -eq 0 ]; then
    emit_deny "Marker write — the '$AGENT_DENIAL_LABEL' agent cannot release a review gate.

$GATE_RELEASE_DENIAL_GUIDANCE"
    exit 0
  fi
  if [ "$MARKER_GATE_SCAN_STATUS" -ne 1 ]; then
    emit_deny "could not determine whether this command invokes marker.sh write/activate (sed/tr may be missing, killed, or errored) — failing closed per this gate's documented fail-closed posture rather than letting an unscanned command bypass gate-release authority."
    exit 0
  fi
fi

# A command whose only mention is the quote-stripped copy has nothing left to
# validate: the traversal guard and Stage 2 read raw text, which lacks the name.
$MARKER_SCRIPT_MENTIONED_IN_RAW_TEXT || exit 0

# Strip leading whitespace. Computed after the exits above so a command that
# never mentions marker.sh in its raw text pays no fork for it.
TRIMMED=$(printf '%s' "$COMMAND" | sed -E 's/^[[:space:]]+//')

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
MARKER_SHAPE='(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+(write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review|cumulative-review|verification)|(activate|deactivate)[[:space:]]+(plan-review|ready-for-review|respond-pr|memory-skill|handoff)|clear-stale([[:space:]]+--dry-run)?|resolve-session-id|status|check[[:space:]]+(code-review|verification))'

# Strict allowlist. Tilde form (~/.claude/scripts/marker.sh) and absolute
# path form (/home/<user>/.claude/scripts/marker.sh) are both accepted.
# No bash wrapper, no env-var prefix, no chain operator, no redirect (except
# trailing `2>/dev/null`), no extra args after the skill name.
VALID_PATTERN="^${MARKER_SHAPE}([[:space:]]+2>/dev/null)?[[:space:]]*\$"

if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qE "$VALID_PATTERN"; then
  exit 0
fi

# Chained-commit allowance. One or more valid `marker.sh write <skill>` shapes
# joined by `&&`, followed by `git commit ...` or `git <merge|rebase|
# cherry-pick|revert> --continue ...`, is the natural atomic form an agent
# types after reviews pass. The tail matches the same verb union as
# _lib_chains_marker_write_before_commit in _lib.sh, which documents why
# `rebase --continue` is included. Chaining marker.sh with anything else
# (curl, rm, redirects, ;) stays denied by falling through to the message
# below.
# Coordinated with require-code-review.sh and require-skill-review.sh, which
# honor the same in-chain marker-write pattern at the commit gate.
#
# Trailing content after the commit-concluding tail is constrained to
# characters that cannot form a further shell chain or redirect
# (`& | ; < >`). Without that constraint the regex would allow
# `marker.sh write X && git commit && curl evil.com`, bypassing the gate's
# own design intent ("no chains to anything but a commit-concluding
# command"). Backticks and `$` (command substitution) remain permitted;
# commit messages containing them are uncommon enough that denying would be
# more disruptive than the marginal forge-vector they represent, and
# substitution is itself gated elsewhere.
# Note: 2>/dev/null is intentionally NOT blessed here. The tail class [^&|;<>]
# already excludes '>' as a security boundary (prevents post-commit redirects like
# `git commit > /path`). A 2>/dev/null exception would require carving out of that
# class with no observed agent friction on the commit-chain form to justify it.
VALID_CHAINED_COMMIT_PATTERN='^((~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)[[:space:]]*&&[[:space:]]*)+git[[:space:]]+(commit|(merge|rebase|cherry-pick|revert)[[:space:]]+--continue)([[:space:]]+[^&|;<>]*)?$'

if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qE "$VALID_CHAINED_COMMIT_PATTERN"; then
  exit 0
fi

# Marker-chain allowance. A chain of two-or-more valid marker.sh shapes
# joined by `&&`, any op/target combination, is permitted — the chain's end
# state is identical to running each op separately, and every op is already
# individually allowlisted (the 20 shapes in permissions.allow) or harmless
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
  ~/.claude/scripts/marker.sh write cumulative-review
  ~/.claude/scripts/marker.sh write verification
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
  ~/.claude/scripts/marker.sh check verification

Chains of valid marker.sh operations joined by && are permitted. Chaining to
any other command (except the blessed 'git commit' or 'git <merge|rebase|
cherry-pick|revert> --continue' tail), or using ||/;, redirects, or extra
args, is denied. Env-var prefix, bash wrapper, and
relative-path forms are not gated here — they are denied by permissions.allow.

To see a result, run the op alone: its stdout and the tool's reported exit
code already carry the verdict. For a multi-line commit message, write it to
a file with the Write tool and chain 'git commit -F' on that file. Never use a
heredoc or -m \"\$(cat ...)\" (code-review's SKILL.md, \"Authoring the commit
message\", gives why)."
