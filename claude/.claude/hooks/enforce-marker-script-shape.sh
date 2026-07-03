#!/bin/bash
# hook-class: gate
# Gate: enforce strict invocation shape for ~/.claude/scripts/marker.sh.
#
# Fail posture: fail-closed — if jq cannot parse the input, deny.
#
# WARNING: Do NOT remove the internal marker.sh check below.
# The "if" field in settings.json is unreliable — it has been observed
# to fire this hook on ALL Bash commands. The internal grep is the actual
# gate. The "if" field is a hint only.
#
# Commands that start directly with the marker.sh path (~/ or absolute) must
# match one of the 14 single-command shapes, the marker.sh write chain to git
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

emit_deny() {
  local reason="$1"
  local reason_json
  reason_json=$(printf '%s' "$reason" | jq -Rs .)
  local payload
  payload=$(printf '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":%s}}' "$reason_json")
  printf '%s\n' "$payload"
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  emit_deny "Blocked by marker-script-shape gate: could not source _lib.sh."
  exit 0
fi

_lib_parse_tool_input_or_deny "Blocked: could not parse tool-input JSON."

# Strip leading/trailing whitespace — computed before the activation guards so
# both the fast-reject and anchored-path check share one computation.
TRIMMED=$(printf '%s' "$COMMAND" | sed -E 's/^[[:space:]]+//')

# Stage 1: cheap substring fast-reject — most Bash calls have no marker mention.
printf '%s' "$COMMAND" | grep -qF 'marker.sh' || exit 0

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
MARKER_SHAPE='(~|/[A-Za-z0-9_./-]+)/\.claude/scripts/marker\.sh[[:space:]]+(write[[:space:]]+(code-review|skill-review|plan-review|ready-for-review)|(activate|deactivate)[[:space:]]+(plan-review|ready-for-review|respond-pr|memory-skill)|clear-stale([[:space:]]+--dry-run)?)'

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
# individually allowlisted (the 12 shapes in permissions.allow) or harmless
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

Chains of valid marker.sh operations joined by && are permitted. Chaining to
any other command (except the blessed 'git commit' tail), or using ||/;,
redirects, or extra args, is denied. Env-var prefix, bash wrapper, and
relative-path forms are not gated here — they are denied by permissions.allow."
