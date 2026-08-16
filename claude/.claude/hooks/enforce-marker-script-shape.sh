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
#   - A Bash-tool file redirect (`printf`/`tee`/`cat` `>` path) that never
#     mentions `marker.sh` bypasses this hook's write-authority check
#     entirely, for any agent type — only the Write/Edit/MultiEdit arm's
#     path-based check catches a direct file write; the Bash arm's Stage-1
#     fast-reject only matches commands containing the literal `marker.sh`
#     substring.
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
#
# WARNING: Do NOT remove the internal marker.sh check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands. The internal grep is the actual
# gate. The "if" field is a hint only.
#
# Commands that start directly with the marker.sh path (~/ or absolute) must
# match one of the 15 single-command shapes, the marker.sh write chain to git
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

    # The patterns below are anchored on the `.claude/<kind>-markers/` SHAPE,
    # not on a `$HOME` prefix, because the same marker file is reachable under
    # more than one path string and a $HOME-anchored glob only recognizes one
    # of them:
    #   - a `..` segment ($HOME/x/../.claude/code-review-markers/m) does not carry
    #     the literal $HOME/.claude/ prefix until something normalizes it, and
    #     realpath is GNU coreutils — absent on stock macOS, where a
    #     $HOME-anchored match would therefore silently allow the write;
    #   - under stow directory-fold, $HOME/.claude is a symlink to the stow
    #     package, so the markers directory is ALSO physically addressable as
    #     <repo>/claude/.claude/<kind>-markers/, which never contains $HOME at
    #     all (see require-worktree-for-file-writes.sh on the fold topology).
    # Matching the shape covers every alias of the same file without depending
    # on normalization succeeding. realpath is still applied as a second
    # candidate and IS load-bearing for one vector the shape match cannot see:
    # a symlink whose own path contains no marker-shaped segment but which
    # resolves into the markers directory (an agent with Bash can create one).
    # Only the resolved path exposes that, so do not "optimize" this call away
    # behind a substring pre-check on the raw path — the shape match covers
    # `..` traversal and the stow fold, not symlink aliasing.
    #
    # That vector is closed only when realpath actually completes: _lib_capped
    # caps it, and on timeout NORMALIZED_TARGET is empty and the loop falls
    # back to the raw path alone. Deliberate — blocking every file write on a
    # stalled $HOME mount is the worse failure — but it means a symlink alias
    # is caught on a healthy filesystem, not unconditionally.
    #
    # Over-matching is the safe direction: this branch only ever produces a
    # deny, and it only runs for agents that may never write marker state at
    # all. The cost of the wider pattern is that one of those agents cannot
    # write an unrelated file that happens to sit in a `.claude/*-markers/`
    # directory — which is not a real path outside this mechanism.
    #
    # _lib_capped wraps realpath because it stats existing path components and
    # this arm now fires on every file write; a stalled $HOME mount (NFS,
    # network home) would otherwise block the tool call with no backstop.
    EXPANDED_TARGET="${TARGET_PATH/#\~/$HOME}"
    NORMALIZED_TARGET=$(_lib_realpath_m "$EXPANDED_TARGET" 2>/dev/null)
    # The `.claude/`-shape match above assumes marker state always sits under
    # a `.claude` path segment, which is true for the default $HOME/.claude
    # resolution but not for a CLAUDE_CONFIG_DIR value with no `.claude`
    # segment (e.g. ~/.config/claude-accounts/<account>) — marker.sh still
    # resolves and writes there via _lib_config_dir(), so this second arm
    # closes that gap. Skipped (not denied) when _lib_config_dir() itself
    # can't resolve, matching this repo's fail-toward-existing-behavior
    # posture for an unresolvable config dir.
    CONFIG_DIR_RESOLVED=$(_lib_config_dir 2>/dev/null) || CONFIG_DIR_RESOLVED=""
    for candidate_path in "$EXPANDED_TARGET" "$NORMALIZED_TARGET"; do
      [ -n "$candidate_path" ] || continue
      case "$candidate_path" in
        # Completion markers (<kind>-markers/) release a gate outright.
        # Active-bypass markers (.<kind>-active.d/) suspend one, which the
        # plan gate honors with no hash comparison at all.
        */.claude/*-markers/*|*/.claude/.*-active.d/*)
          emit_deny "Marker write denied: the '$AGENT_TYPE' agent cannot release a review gate by writing '$TARGET_PATH'.

$GATE_RELEASE_DENIAL_GUIDANCE"
          exit 0
          ;;
      esac
      if [ -n "$CONFIG_DIR_RESOLVED" ]; then
        case "$candidate_path" in
          "$CONFIG_DIR_RESOLVED"/*-markers/*|"$CONFIG_DIR_RESOLVED"/.*-active.d/*)
            emit_deny "Marker write denied: the '$AGENT_TYPE' agent cannot release a review gate by writing '$TARGET_PATH'.

$GATE_RELEASE_DENIAL_GUIDANCE"
            exit 0
            ;;
        esac
      fi
    done
    exit 0
    ;;
  Bash) ;;
  *) exit 0 ;;
esac

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
MARKER_SHAPE='(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+(write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)|(activate|deactivate)[[:space:]]+(plan-review|ready-for-review|respond-pr|memory-skill)|clear-stale([[:space:]]+--dry-run)?|resolve-session-id)'

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
# individually allowlisted (the 13 shapes in permissions.allow) or harmless
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
  ~/.claude/scripts/marker.sh deactivate plan-review
  ~/.claude/scripts/marker.sh deactivate ready-for-review
  ~/.claude/scripts/marker.sh deactivate respond-pr
  ~/.claude/scripts/marker.sh deactivate memory-skill
  ~/.claude/scripts/marker.sh clear-stale
  ~/.claude/scripts/marker.sh clear-stale --dry-run
  ~/.claude/scripts/marker.sh resolve-session-id

Chains of valid marker.sh operations joined by && are permitted. Chaining to
any other command (except the blessed 'git commit' tail), or using ||/;,
redirects, or extra args, is denied. Env-var prefix, bash wrapper, and
relative-path forms are not gated here — they are denied by permissions.allow."
