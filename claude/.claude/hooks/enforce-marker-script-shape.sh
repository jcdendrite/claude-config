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
#     `cp`/`mv`/`install`/`dd`/`sed`/`curl`/`wget`/`rsync`/`scp`/`openssl`
#     (every word of the fragment is a candidate, not just an identified
#     destination -- see _lib.sh's _lib_fragment_candidates for why) is
#     caught by a dedicated scan that runs before Stage 1, independent of
#     the command mentioning `marker.sh`. Every word of a write-gated
#     fragment being a candidate also means a command that merely READS a
#     marker path as a non-destination argument (e.g. `cp
#     ~/.claude/code-review-markers/x /tmp/backup`) is denied too for a
#     no-gate-release agent, even though it is not a write -- a `cat`,
#     `grep`, or `less` read of the same path is unaffected (it invokes none
#     of the recognized write utilities), so a denied read has an
#     actionable alternative. Two residuals remain open, neither closed by
#     the addition of curl/wget/rsync/scp/openssl to the recognized set:
#     (a) a URL/server-derived destination basename (`curl -O URL`, bare
#     `wget URL`) -- the write target is never a literal token in the
#     command; (b) a relative destination token when the process's cwd
#     (from the tool-call payload) sits inside a marker directory --
#     candidate extraction never joins a candidate to that cwd. A directory
#     destination via `-t DIR`/`--target-directory=DIR`, a bare `cp file
#     DIR` with no trailing slash, and a plain trailing-slash form (`cp
#     file ~/.claude/code-review-markers/`) are all caught even though the
#     joined DIR/basename path this actually writes to is never a single
#     literal token in the command: cp/mv/install/rsync/scp's own
#     basename-preservation semantics land the write inside the marker
#     directory regardless of trailing slash, and `_lib_shape_match`
#     `-ef`-compares the DIR token itself against the marker directory
#     (Pass 4) alongside the trailing-slash form's own tail-glob match
#     (Pass 2's tail-glob absorbing the trailing slash's empty leaf).
#     Still open beyond the utility-recognition class: `>|`
#     (clobber-override -- its literal `|` gets severed from the operator by
#     the fragment splitter this scan reuses, before extraction ever sees a
#     whole token); `python3 -c "open(...).write(...)"` and here-doc bodies
#     handed to an interpreter; a `$(...)`-computed target path;
#     shell-function/variable indirection around the write utility itself;
#     and any write-capable utility outside this named set entirely --
#     `aws s3 cp`, `git archive`, `docker cp`, `sftp`, `ftp`, `tar` writing
#     through a pipe to a shell that redirects, or anything else with its
#     own destination-argument syntax -- this scan is a fixed name list, not
#     a general write-syscall trace, so a program not on the list is
#     entirely unscanned.
#   - `_lib_shape_match`'s `-ef`-based inode-identity checks (shared with
#     enforce-config-write-shape.sh): a `..` path segment through a
#     not-yet-created directory has no inode to stat yet — narrow, since in
#     nearly every such case the write itself would ENOENT first.
#   - `-ef` has no timeout backstop, so a hung network mount can block the
#     check indefinitely — a fully hung D-state mount was never
#     interruptible either.
#   - Only `$HOME`/`${HOME}`/`$CLAUDE_CONFIG_DIR`/`${CLAUDE_CONFIG_DIR}` are
#     expanded in candidate text; every other shell-variable reference stays
#     under the shell-function/variable indirection gap above.
#   - The number of `_lib_shape_match`/`-ef` calls per Bash command is
#     unbounded — see `_lib.sh`'s own disclosure on `_lib_shape_match` for
#     why this is deliberate.
#   - A hardlink at an arbitrary, non-marker-shaped path (e.g. `/tmp/x`)
#     pointing at one specific real, already-existing marker file's inode is
#     not caught: doing so would require enumerating every file under every
#     `*-markers/`/`.*-active.d/` directory to `-ef` the candidate against,
#     an unbounded-cost operation on directories this repo's own
#     `_lib_marker_value_present` comment already documents as reaching
#     13k-30k entries. A hardlink placed AT a marker-shaped path (inside the
#     real directory) is unaffected by this gap and still denies.
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
#   - COMMAND_UNQUOTED's sed/tr strip and _bash_marker_redirect_candidates's
#     own _lib_split_fragments call both check their exit status and fail
#     closed, matching deny-network-installs.sh's
#     COMMAND_UNQUOTED_EXIT/FRAGMENTS_SPLIT_EXIT pattern.
#
# Posture: raises the cost of a naive/cooperative write to a marker path,
# not a hard boundary — every gap above traces to this being a fixed
# name-list text scan rather than a syscall trace, so an interpreter write,
# here-doc, `$(...)`-computed path, or unlisted write utility passes through
# untouched.
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

DENY_GATE_LABEL="marker-script-shape"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny.
# Re-pointed at _lib.sh's _lib_emit_deny immediately after a successful
# source — see _lib_parse_tool_input_or_deny's contract comment in _lib.sh
# for why the full jq-encode-or-hard-block body lives there, not here.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
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
GATE_RELEASE_DENIAL_GUIDANCE="Releasing a gate requires having run the review that the gate demands, and this agent type could not have run it — either it carries no Skill tool and cannot invoke a review skill at all, or it is dispatched read-only by mandate. Either way a marker it writes would assert a review that never happened. Marker writes are attributed to the parent session, so this would release the gate for the whole session, not just this subagent.

Report the denial to the dispatching session instead: name the gate that blocked you, the command or path it blocked, and what you had completed. The dispatching session runs the review skill (or delegates it to a general-purpose subagent, which does carry Skill) and re-dispatches you.

Matching a hash you computed yourself is not authorization — an equal hash shows the state is unchanged, not that anyone reviewed it.

If you only meant to read a marker file rather than write one, use cat/grep/less instead — this gate scans for write utilities, not reads."

# _marker_shape_match TARGET_PATH
# True (exit 0) iff TARGET_PATH matches the marker-directory SHAPE
# (completion markers under <kind>-markers/, or active-bypass markers under
# .<kind>-active.d/), so both call sites (Write/Edit/MultiEdit's single
# target below, the Bash redirect/utility arm's several extracted targets
# further down) share one pattern that cannot drift between them.
# Exit 1: no match. Exit 2: the Claude Code config directory could not be
# resolved, so a config-dir-relative marker alias could not be ruled out —
# callers must deny, not skip, on exit 2. Shape test only: no agent-type
# read, no deny decision — callers decide what a match or a resolution
# failure means.
#
# A thin fixed-argument wrapper around the shared `_lib_shape_match` engine
# in `_lib.sh` — the nocasematch scoping, `-ef`-based inode-identity checks,
# and config-dir-aware shape variants live there once, shared with
# `enforce-config-write-shape.sh`, rather than as two independently
# maintained copies of this intricate, security-critical candidate-
# resolution logic. See `_lib_shape_match`'s own header for the full
# contract (detection-strategy passes, over-matching safety, and why a
# resolution failure denies unconditionally).
_marker_shape_match() {
  _lib_shape_match "$1" '*-markers/*' '.*-active.d/*'
}

# Defense-in-depth: filter on tool name here rather than relying on the
# settings.json matchers alone. Anything that is neither a file-write tool nor
# Bash cannot reach marker state.
#
# Both arms key on .agent_type; neither pays a per-fire subprocess for it any
# more, since _lib_parse_tool_input_or_deny already populates AGENT_TYPE
# (and, for this arm, FILE_PATH) from the single shared parse every hook
# invocation already pays for.
case "$TOOL_NAME" in
  Write|Edit|MultiEdit)
    # Path-based arm. Every marker lives under a known directory, so the
    # decision is "is this agent writing marker state?" — a question the
    # resolved path answers directly, with no command text to outsmart.
    TARGET_PATH="$FILE_PATH"

    _lib_is_no_gate_release_agent "$AGENT_TYPE" || exit 0
    [ -n "$TARGET_PATH" ] || exit 0

    # Shape-tested via _marker_shape_match (defined above; shared with the
    # Bash redirect/utility arm below, including its config-dir-resolution-
    # failure exit code) — see its comment for the pattern rationale.
    _marker_shape_match "$TARGET_PATH"
    case "$?" in
      0)
        emit_deny "Marker write — the '$AGENT_TYPE' agent cannot release a review gate by writing '$TARGET_PATH'.

$GATE_RELEASE_DENIAL_GUIDANCE"
        ;;
      2)
        emit_deny "Marker write — could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) to verify '$TARGET_PATH' is not a review-marker path."
        ;;
    esac
    exit 0
    ;;
  Bash) ;;
  *) exit 0 ;;
esac

# _bash_marker_fragment_candidates FRAGMENT
# Emits, one per line, every write-target word in FRAGMENT worth
# shape-testing: `>`/`>>` operands (bare or glued, fd-prefixed), `tee`'s own
# non-flag arguments, and — for every other write utility in
# `_LIB_WRITE_UTILITIES` this invocation could use to write a file — every
# word of the fragment, not an identified destination token. Over-emission
# is safe — each candidate is independently shape-tested by
# _marker_shape_match.
#
# A thin wrapper around the shared `_lib_fragment_candidates` engine in
# `_lib.sh` — see that function's own header for the extraction fixture and
# detection-rule detail.
_bash_marker_fragment_candidates() {
  _lib_fragment_candidates "$1"
}

# _bash_marker_redirect_candidates COMMAND_UNQUOTED
# Splits COMMAND_UNQUOTED into fragments (the same split _lib_split_fragments
# gives deny-network-installs.sh) and emits every fragment's candidate
# write-target words, one per line. Returns _lib_split_fragments's own exit
# status on failure -- the caller checks it and denies at the top level;
# see the comment below for why this function cannot emit_deny itself.
#
# A thin wrapper around the shared `_lib_redirect_candidates` engine in
# `_lib.sh`, shared with `enforce-config-write-shape.sh`.
_bash_marker_redirect_candidates() {
  _lib_redirect_candidates "$1"
}

# Runs unconditionally on every Bash call, not gated behind a `.claude`-
# substring pre-filter: that pre-filter (both a Stage-0 command-level grep
# and a per-candidate check) was the exact bypass surface closed by this
# redesign — a config-dir-relative marker alias, or a symlink whose own path
# carries no `.claude` segment, never contained the literal substring the
# old pre-filter required. _lib_redirect_candidates's own
# _lib_command_has_write_construct fast-reject (fork-free) is what now keeps
# an ordinary non-write Bash call cheap instead.
#
# Bash-tool write to a marker path via a redirect or write utility that
# never mentions `marker.sh` — closes the class of bypass Stage 1's
# substring gate below would otherwise fast-exit as an allow. Runs first
# for that reason.
# Invariant: every raw-text DETECTION check in this file must read
# COMMAND_UNQUOTED, never raw $COMMAND — a shell quote landing inside the
# `marker.sh` token (e.g. `~/.claude/scripts/"marker".sh write code-review`)
# defeats a substring/regex match against unstripped text while executing
# identically to the unquoted form. $COMMAND/TRIMMED stay reserved for the
# allowlist arm's own pattern matches and for message/display text.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
COMMAND_UNQUOTED_EXIT=$?
if [ "$COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach marker state."
  exit 0
fi
MARKER_WRITE_REDIRECT_CANDIDATES=$(_bash_marker_redirect_candidates "$COMMAND_UNQUOTED")
MARKER_WRITE_REDIRECT_CANDIDATES_EXIT=$?
if [ "$MARKER_WRITE_REDIRECT_CANDIDATES_EXIT" -ne 0 ]; then
  emit_deny "could not split the command into fragments (exit ${MARKER_WRITE_REDIRECT_CANDIDATES_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach marker state."
  exit 0
fi
while IFS= read -r MARKER_WRITE_CANDIDATE; do
  [ -n "$MARKER_WRITE_CANDIDATE" ] || continue
  _marker_shape_match "$MARKER_WRITE_CANDIDATE"
  MARKER_WRITE_SHAPE_STATUS=$?
  if [ "$MARKER_WRITE_SHAPE_STATUS" -eq 2 ]; then
    MARKER_WRITE_CANDIDATE_TRUNCATED=$(printf '%s' "$MARKER_WRITE_CANDIDATE" | cut -c1-80)
    emit_deny "Marker write — could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) to verify '$MARKER_WRITE_CANDIDATE_TRUNCATED' is not a review-marker path."
    exit 0
  fi
  [ "$MARKER_WRITE_SHAPE_STATUS" -eq 0 ] || continue
  # AGENT_TYPE is already populated by _lib_parse_tool_input_or_deny's
  # shared parse, at no added per-fire cost.
  if _lib_is_no_gate_release_agent "$AGENT_TYPE"; then
    MARKER_WRITE_CANDIDATE_TRUNCATED=$(printf '%s' "$MARKER_WRITE_CANDIDATE" | cut -c1-80)
    emit_deny "Marker write — the '$AGENT_TYPE' agent cannot release a review gate by writing '$MARKER_WRITE_CANDIDATE_TRUNCATED'.

$GATE_RELEASE_DENIAL_GUIDANCE"
    exit 0
  fi
# Here-string over the already-captured MARKER_WRITE_REDIRECT_CANDIDATES,
# not a nested command substitution: the split's exit status is checked
# above, before this loop starts, matching
# _bash_marker_redirect_candidates's own inner loop.
done <<< "$MARKER_WRITE_REDIRECT_CANDIDATES"

# Strip leading/trailing whitespace — computed before the activation guards so
# both the fast-reject and anchored-path check share one computation.
# TRIMMED_EXIT is captured immediately (a later command would clobber $?) but
# checked just above the traversal guard below, its first actual consumer —
# not here — so a status-2 deny from the gate-release-authority arm's own,
# more specific check (which runs in between and does not depend on TRIMMED)
# still takes precedence when IT is what caught a sed failure.
TRIMMED=$(printf '%s' "$COMMAND" | sed -E 's/^[[:space:]]+//')
TRIMMED_EXIT=$?

# Stage 1: cheap substring fast-reject — most Bash calls have no marker mention.
# Matches COMMAND_UNQUOTED (quote-stripped), not raw $COMMAND: a quote
# landing inside the `marker.sh` token (e.g.
# `~/.claude/scripts/"marker".sh write ...`) breaks the contiguous
# substring in raw text while executing identically to the unquoted form,
# and this fast-reject exiting early would skip every check below.
# Case-folded (-i): on a case-insensitive-but-case-preserving filesystem
# (macOS APFS/HFS+, Windows NTFS), Marker.sh opens the same file as
# marker.sh -- a case-sensitive fast-reject here would skip this hook's
# entire deep validation below, not just the gate-release-authority check.
printf '%s' "$COMMAND_UNQUOTED" | grep -qFi 'marker.sh' || exit 0

# Bash arm of the gate-release authority check. Placed immediately after
# Stage 1 and BEFORE Stage 2, deliberately: Stage 2 fast-exits wrapped forms
# (bash -c, env-var prefix, relative path) and leaves them to
# permissions.allow, so a check placed after it would inherit that hole.
# Two independent detectors, unconditionally OR'd together — neither
# subsumes the other:
#   - Raw-text substring match against COMMAND_UNQUOTED (quote-stripped),
#     matching the op keyword anywhere in the command text. This is what
#     catches a wrapper-hole invocation (`bash -c "marker.sh write
#     code-review"`, `eval "marker.sh write ..."`), including one that
#     itself quote-splits the wrapped text (`bash -c "mark""er.sh write
#     code-review"`) — general to any `<shell> -c "..."` / `eval "..."`
#     wrapper, not bash-specific, since _lib_fragment_command_word's runner
#     list excludes bash/sh/zsh/dash/ksh entirely and so cannot see inside
#     any of them.
#   - Command-word match via _lib_command_invokes_tool_subcmd, which
#     resolves the fragment's actual command word from raw $COMMAND after
#     its own internal quote-stripping. This is what catches a top-level
#     quote-split invocation (`"marker.sh" write code-review`,
#     `~/.claude/scripts/"marker".sh write code-review`) via positional
#     command-word resolution rather than the substring check's blind scan.
# _lib_command_invokes_tool_subcmd's SUBCMD... sequence-matches
# positionally from index 0, so a single call passing both ops together
# (`marker.sh write activate`) would require the literal two-word sequence
# "write activate" and never match a real single-op invocation. Two
# calls, one per op, are what its actual contract requires. Status 2
# (could not determine, e.g. sed/tr missing) denies, matching this hook's
# fail-closed posture rather than silently falling through as "no match."
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
# AGENT_TYPE is already populated by _lib_parse_tool_input_or_deny's shared
# parse, at no added per-fire cost.
if _lib_is_no_gate_release_agent "$AGENT_TYPE"; then
  MARKER_GATE_MATCHED=false
  MARKER_GATE_INDETERMINATE=false

  # Case-folded (-i): same case-insensitive-filesystem rationale as Stage 1's
  # fast-reject above. Matches COMMAND_UNQUOTED, not raw $COMMAND — see the
  # comment block above this arm for why.
  printf '%s' "$COMMAND_UNQUOTED" | grep -qEi 'marker\.sh[[:space:]]+(write|activate)' \
    && MARKER_GATE_MATCHED=true

  for MARKER_GATE_OP in write activate; do
    _lib_command_invokes_tool_subcmd "$COMMAND" marker.sh "$MARKER_GATE_OP"
    MARKER_GATE_OP_STATUS=$?
    if [ "$MARKER_GATE_OP_STATUS" -eq 0 ]; then
      MARKER_GATE_MATCHED=true
    elif [ "$MARKER_GATE_OP_STATUS" -ne 1 ]; then
      MARKER_GATE_INDETERMINATE=true
    fi
  done

  if $MARKER_GATE_MATCHED; then
    emit_deny "Marker write — the '$AGENT_TYPE' agent cannot release a review gate.

$GATE_RELEASE_DENIAL_GUIDANCE"
    exit 0
  fi
  if $MARKER_GATE_INDETERMINATE; then
    emit_deny "could not determine whether '${COMMAND:0:200}' invokes marker.sh write/activate (sed/tr may be missing, killed, or errored) — failing closed per this gate's documented fail-closed posture rather than letting an unscanned command bypass gate-release authority."
    exit 0
  fi
fi

# Checked here, at TRIMMED's first actual use, rather than right after its
# assignment above: an unchecked failure would silently empty TRIMMED, which
# the traversal guard and Stage 2's anchor below would then read as "no
# marker.sh prefix" and allow through — skipping every check from here
# through the deep VALID_PATTERN allowlist on a mere sed hiccup, which
# contradicts this hook's own documented fail-closed posture.
if [ "$TRIMMED_EXIT" -ne 0 ]; then
  emit_deny "could not trim leading whitespace from the command text (exit ${TRIMMED_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than skipping this hook's deep marker.sh-shape validation entirely."
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

# Stage 2: anchored leading-path check. Matches COMMAND_UNQUOTED (quote-
# stripped), not raw $COMMAND/TRIMMED: Claude Code's own permission matcher
# normalizes quoting before comparing against permissions.allow's exact
# literals, so a quote-split top-level invocation
# (`~/.claude/scripts/"marker".sh write ...`) is not reliably caught by the
# wrapped-forms fast-exit below the way a genuinely wrapped form is —
# matching it here instead routes it into this hook's own deep validation.
# Leading whitespace is matched inline (`^[[:space:]]*`) rather than via a
# separately trimmed variable, since COMMAND_UNQUOTED, unlike TRIMMED, is
# not leading-whitespace-stripped.
# Bash =~ treats the subject as a single string; `^` anchors at position 0
# only — correct for multi-line $COMMAND (heredocs) because grep -E with
# '^' matches per-line and would over-activate on a heredoc body whose
# inner line starts with the script path.
# Wrapped/chained forms (bash -c, env-var prefix, semicolons, subshells)
# still intentionally fast-exit here — quote-stripping does not reshape
# those into an anchored top-level path — and permissions.allow is their
# gate: those wrapper executables are not in the allow list, so the
# permission layer denies them before this hook's deep validation would
# ever matter.
# nocasematch: same case-insensitive-filesystem rationale as Stage 1's
# fast-reject above -- unmatched here falls through to the wrapped-forms
# fast-exit below, the same silent-allow shape Stage 1 guards against.
# Scoped tightly around this one statement and restored immediately after.
shopt -s nocasematch
STAGE2_ANCHOR_MATCHED=1
if [[ "$COMMAND_UNQUOTED" =~ ^[[:space:]]*(\~|\$HOME|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh([[:space:]]|$) ]]; then
  STAGE2_ANCHOR_MATCHED=0
fi
shopt -u nocasematch
if [ "$STAGE2_ANCHOR_MATCHED" -ne 0 ]; then
  exit 0
fi

# Path prefix + one valid (op, target) shape — no anchors, no trailing
# suffix. Shared building block for VALID_PATTERN and the marker-chain
# pattern below, so the path-prefix regex fragment has one authoritative copy.
MARKER_SHAPE='(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+(write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review|cumulative-review)|(activate|deactivate)[[:space:]]+(plan-review|ready-for-review|respond-pr|memory-skill|handoff)|clear-stale([[:space:]]+--dry-run)?|resolve-session-id|status|check[[:space:]]+code-review)'

# Strict allowlist. Tilde form (~/.claude/scripts/marker.sh) and absolute
# path form (/home/<user>/.claude/scripts/marker.sh) are both accepted.
# No bash wrapper, no env-var prefix, no chain operator, no redirect (except
# trailing `2>/dev/null`), no extra args after the skill name.
VALID_PATTERN="^${MARKER_SHAPE}([[:space:]]+2>/dev/null)?[[:space:]]*\$"

# Case-folded (-i): same case-insensitive-filesystem rationale as Stage 1's
# fast-reject above -- without it, a legitimate case-varied invocation this
# far past Stage 1/2 would fall through to the deny at the bottom instead of
# matching its own allowlisted shape.
if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qEi "$VALID_PATTERN"; then
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

# Case-folded (-i): same case-insensitive-filesystem rationale as VALID_PATTERN above.
if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qEi "$VALID_CHAINED_COMMIT_PATTERN"; then
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

# Case-folded (-i): same case-insensitive-filesystem rationale as VALID_PATTERN above.
if [[ "$TRIMMED" != *$'\n'* ]] && printf '%s' "$TRIMMED" | grep -qEi "$VALID_MARKER_CHAIN_PATTERN"; then
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
