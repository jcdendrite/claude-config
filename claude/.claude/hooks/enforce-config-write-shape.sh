#!/bin/bash
# hook-class: gate
# Gate: deny agent-mediated writes to claude-config.toml, this repo's
# consolidated config-key state file. Two jobs:
#   1. Deny a Write/Edit/MultiEdit whose resolved target IS the resolved
#      claude-config.toml path.
#   2. Deny a Bash command that invokes _config_set by name, and a Bash
#      command whose redirect/utility-write candidates resolve to the same
#      path, independent of whether _config_set is named anywhere in the
#      command.
#
# Wired on the same Bash and Write|Edit|MultiEdit PreToolUse matchers as
# enforce-marker-script-shape.sh. Shares that hook's shape-matching and
# redirect-candidate-extraction engine via _lib.sh's _lib_shape_match/
# _lib_redirect_candidates rather than a second, independently maintained
# copy of that intricate, security-critical logic.
#
# Only Claude-Code-tool-mediated writes are denied; a human editing the file
# directly is unaffected.
#
# Fail posture: fail-closed — if jq cannot parse the input, deny.
#
# No agent-type carve-out (unlike enforce-marker-script-shape.sh's
# gate-release logic): every caller, main session or subagent, is denied
# from writing this file via a Claude Code tool call. This hook is a
# blanket write-path denial, not an authority-based one.
#
# Known gaps this hook does NOT close (inherited from
# enforce-marker-script-shape.sh's own documented limitations, since this
# hook shares its redirect-candidate engine — see that file's header for
# the fuller catalog this one does not repeat):
#   - Shell-level indirection around the `_config_set` function name
#     (a variable or function wrapper) is not matched by the raw name-scan
#     below.
#   - A $(...)-computed target path is not resolved by the Bash arm's
#     candidate extraction.
#   - Script-file indirection: `printf ... > /tmp/x.sh && bash /tmp/x.sh`
#     evades command-text scanning entirely, since a PreToolUse hook sees
#     only the top-level Bash `command` string — the same accepted
#     limitation enforce-marker-script-shape.sh's header discloses for
#     `python3 -c "open(...).write(...)"` and here-doc bodies.
#   - migrate-legacy-config.sh's own name is not scanned for: mentioning a
#     script's name in command text is not itself a write, and any write it
#     makes internally is invisible to this hook regardless — the same
#     script-indirection gap disclosed just above.
#   - _lib_shape_match's `-ef`-based inode-identity checks (shared with
#     enforce-marker-script-shape.sh) have two residuals: (a) a `..` path
#     segment through a not-yet-created directory has no inode to stat yet
#     (narrow — in nearly every such case the write itself would ENOENT
#     first), and (b) `-ef` has no timeout backstop at all, unlike the prior
#     `_lib_capped`-wrapped `realpath` design — a real regression against a
#     merely slow-but-responsive network stat, though unchanged against a
#     fully-hung (D-state) mount, which a `timeout`-wrapped external process
#     could never interrupt either. Only
#     `$HOME`/`${HOME}`/`$CLAUDE_CONFIG_DIR`/`${CLAUDE_CONFIG_DIR}`
#     are expanded in candidate text; every other shell-variable reference
#     stays under the shell-indirection gap above.
#
# WARNING: Do NOT remove the internal _config_set/redirect-candidate checks
# below. The "if" field in settings.json is unreliable — see
# enforce-marker-script-shape.sh's identical warning for the observed
# failure mode. The internal checks are the actual gate; "if" is a hint only.
set -uo pipefail

DENY_GATE_LABEL="config-write-shape"

# Minimal bootstrap so a failed `source` of _lib.sh below can still deny —
# see enforce-marker-script-shape.sh's identical stub for why this shape.
emit_deny() {
  printf 'Blocked by %s gate: %s\n' "$DENY_GATE_LABEL" "$1" >&2
  exit 2
}

if ! . "$(dirname "$0")/_lib.sh" 2>/dev/null; then
  # shellcheck disable=SC2218
  emit_deny "could not source _lib.sh."
fi
emit_deny() { _lib_emit_deny "$1"; }

_lib_parse_tool_input_or_deny "could not parse tool-input JSON."

# Defense-in-depth: filter on tool name here rather than relying on the
# settings.json matchers alone. Anything that is neither a file-write tool
# nor Bash cannot reach the config state file through this hook's own logic.
case "$TOOL_NAME" in
  Write|Edit|MultiEdit)
    TARGET_PATH="$FILE_PATH"
    [ -n "$TARGET_PATH" ] || exit 0
    _lib_shape_match "$TARGET_PATH" 'claude-config.toml'
    case "$?" in
      0)
        emit_deny "Config-file write — '$TARGET_PATH' is (or resolves to) this machine's claude-config.toml. Agent-mediated writes to the consolidated config-key state file are denied; a human editing it directly, outside Claude Code, is unaffected. Use install.sh's interactive opt-in prompt or migrate-legacy-config.sh instead."
        ;;
      2)
        emit_deny "Config-file write — could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) to verify '$TARGET_PATH' is not the claude-config.toml state file."
        ;;
    esac
    exit 0
    ;;
  Bash) ;;
  *) exit 0 ;;
esac

# Redirect/utility-write scan, run unconditionally on every Bash command —
# independent of whether _config_set is named anywhere in the command (e.g.
# a plain `printf 'autonomous_shipping = true\n' >> ~/.claude/claude-config.toml`,
# which contains no `_config_set` substring at all). Mirrors
# enforce-marker-script-shape.sh's own redirect-scan structure exactly, via
# the shared _lib.sh engine.
#
# Invariant: every raw-text DETECTION check in this file must read
# COMMAND_UNQUOTED, never raw $COMMAND — a shell quote landing inside a
# scanned token (e.g. `_config""_set`) defeats a substring match against
# unstripped text while executing identically to the unquoted form.
# $COMMAND stays reserved for message/display text only.
COMMAND_UNQUOTED=$(_lib_strip_shell_quotes "$COMMAND")
COMMAND_UNQUOTED_EXIT=$?
if [ "$COMMAND_UNQUOTED_EXIT" -ne 0 ]; then
  emit_deny "could not quote-strip the command text (exit ${COMMAND_UNQUOTED_EXIT}) — sed/tr may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach the config state file."
  exit 0
fi

CONFIG_WRITE_REDIRECT_CANDIDATES=$(_lib_redirect_candidates "$COMMAND_UNQUOTED")
CONFIG_WRITE_REDIRECT_CANDIDATES_EXIT=$?
if [ "$CONFIG_WRITE_REDIRECT_CANDIDATES_EXIT" -ne 0 ]; then
  emit_deny "could not split the command into fragments (exit ${CONFIG_WRITE_REDIRECT_CANDIDATES_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach the config state file."
  exit 0
fi
while IFS= read -r CONFIG_WRITE_CANDIDATE; do
  [ -n "$CONFIG_WRITE_CANDIDATE" ] || continue
  _lib_shape_match "$CONFIG_WRITE_CANDIDATE" 'claude-config.toml'
  CONFIG_WRITE_SHAPE_STATUS=$?
  if [ "$CONFIG_WRITE_SHAPE_STATUS" -eq 2 ]; then
    CONFIG_WRITE_CANDIDATE_TRUNCATED=$(printf '%s' "$CONFIG_WRITE_CANDIDATE" | cut -c1-80)
    emit_deny "Config-file write — could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) to verify '$CONFIG_WRITE_CANDIDATE_TRUNCATED' is not the claude-config.toml state file."
    exit 0
  fi
  if [ "$CONFIG_WRITE_SHAPE_STATUS" -eq 0 ]; then
    CONFIG_WRITE_CANDIDATE_TRUNCATED=$(printf '%s' "$CONFIG_WRITE_CANDIDATE" | cut -c1-80)
    emit_deny "Config-file write — '$CONFIG_WRITE_CANDIDATE_TRUNCATED' is (or resolves to) this machine's claude-config.toml. Agent-mediated writes to the consolidated config-key state file are denied; a human editing it directly, outside Claude Code, is unaffected."
    exit 0
  fi
# Here-string over the already-captured CONFIG_WRITE_REDIRECT_CANDIDATES,
# not a nested command substitution — matches
# _bash_marker_redirect_candidates's own inner-loop precedent: the split's
# exit status is checked above, before this loop starts.
done <<< "$CONFIG_WRITE_REDIRECT_CANDIDATES"

# Name-scan: denies any command mentioning `_config_set` as a literal
# substring, quote-blind (tested against the already quote-stripped command
# text above). Deliberately a bare substring test, not a positional-argument-
# grammar parser like marker.sh's — there is no legitimate Claude-Code-Bash-
# tool-call shape that types `_config_set` directly (the two sanctioned
# callers, install.sh's interactive prompt and migrate-legacy-config.sh's
# import phase, invoke it from inside a sourced/executed script, never as a
# literal top-level Bash command token), so no argument-shape allowlist is
# needed here the way marker.sh's chain/op/target shapes are.
if printf '%s' "$COMMAND_UNQUOTED" | grep -qF '_config_set'; then
  emit_deny "Config-file write — command invokes _config_set, the sole sanctioned writer for claude-config.toml. Agent-mediated calls to it are denied; use install.sh's interactive opt-in prompt or migrate-legacy-config.sh instead."
fi

exit 0
