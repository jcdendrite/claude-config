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
# gate-release logic): every caller, main session or subagent, invoking
# this file's write path in a literal, non-indirected form is denied from
# writing this file via a Claude Code tool call — see the shell-variable-
# and script-file-indirection gaps disclosed below. This hook is a blanket
# write-path denial, not an authority-based one.
#
# Known gaps this hook does NOT close (inherited from
# enforce-marker-script-shape.sh's own documented limitations, since this
# hook shares its redirect-candidate engine — see that file's header for
# the fuller catalog this one does not repeat, including the exact set of
# write utilities recognized today (`tee`, `cp`/`mv`/`install`/`dd`/`sed`/
# `curl`/`wget`/`rsync`/`scp`/`openssl`) and the residual gap that any other
# write-capable utility outside that fixed name list is entirely unscanned.
#   - This hook's suffix pattern is a bare filename (`claude-config.toml`),
#     not a glob ending in a trailing segment like the marker patterns'
#     `*-markers/*` — narrower matching than the marker hook's own glob
#     shape. `_lib_shape_match`'s own dedicated pass for a 1-component
#     pattern (see that function's header) closes the resulting
#     directory-destination gap (`cp`/`mv`/`install`/`rsync`/`scp` with a
#     trailing-slash directory target, or `-t DIR`/`--target-directory=DIR`)
#     for a candidate `-ef`-comparable to the resolved config root; a
#     remote-host-prefixed destination (`scp file host:~/.claude/`) is not
#     `-ef`-comparable to a local path and stays open. That pass matches on
#     the destination alone, with no visibility into the write's source
#     file name. This hook's own calling code additionally requires the
#     command text to name `claude-config.toml` before honoring a
#     directory-root match, so a write of an unrelated file into the config
#     root via one of these utilities is allowed, not denied.
#   - A URL/server-derived destination basename (`curl -O URL`, bare
#     `wget URL`) is open the same way as in the marker hook — see that
#     file's header for why.
#   - A relative destination when the process cwd sits inside the config
#     directory is open the same way as in the marker hook.
#   - The Bash arm's redirect/utility-write scan emits every word of a
#     write-gated fragment as a candidate (see _lib.sh's
#     _lib_fragment_candidates for why), not only the true destination. A
#     command that merely READS claude-config.toml as a non-destination
#     argument (e.g. `cp ~/.claude/claude-config.toml /tmp/backup.toml`) is
#     denied too, even though it is not a write. A `cat`, `grep`, or `less`
#     read of the file is unaffected by this hook, since it invokes none of
#     the recognized write utilities, so a denied read has an actionable
#     alternative.
#   - A braced write-utility-fragment argument (`{a,b}`, `{a..b}`) denies
#     whether or not it resolves to a protected path once bash actually
#     expands it — over-matching, not a coverage gap: `_lib.sh`'s
#     `_lib_command_has_brace_expansion_bypass` detects the construct's
#     shape rather than expanding it, so `cp src/{a,b}.txt /tmp/` denies
#     even though only one of the two expanded targets is real.
#   - A quoted brace the real shell would NOT itself expand also denies,
#     since quote-stripping only deletes quote characters and leaves the
#     brace, comma, and slash the predicate keys on untouched.
#   - The predicate scans heredoc bodies too, with no notion of a
#     heredoc-body boundary: a `cat <<EOF > /path/to/file.json` writing
#     ordinary `{"a": 1, "b": 2}`-shaped JSON denies whenever a recognized
#     write utility and a `/` both appear anywhere in the same command.
#   - A write-utility name or path token spelled via brace-expansion
#     syntax (`_config_s{et,et}`) is caught via COMMAND_FLATTENED, a
#     16-pass-flattened companion every raw-text scan below is paired
#     with — see `_lib.sh`'s `_lib_brace_flatten`.
#   - Shell-level indirection around the `_config_set` function name
#     (a variable or function wrapper) is not matched by the raw name-scan
#     below.
#   - A $(...)-computed target path is not resolved by the Bash arm's
#     candidate extraction.
#   - Script-file indirection: `printf ... > /tmp/x.sh && bash /tmp/x.sh`
#     evades command-text scanning entirely, since a PreToolUse hook sees
#     only the top-level Bash `command` string — the same accepted
#     limitation enforce-marker-script-shape.sh's header discloses for
#     `python3 -c "open(...).write(...)"` and here-doc bodies. Closing this
#     would also deny migrate-legacy-config.sh's own sanctioned _config_set
#     calls, which reach the file through the same indirection.
#   - migrate-legacy-config.sh's own name is not scanned for: mentioning a
#     script's name in command text is not itself a write, and any write it
#     makes internally is invisible to this hook regardless — the same
#     script-indirection gap disclosed just above.
#   - _lib_shape_match's `-ef`-based inode-identity checks (shared with
#     enforce-marker-script-shape.sh): a `..` path segment through a
#     not-yet-created directory has no inode to stat yet — narrow, since in
#     nearly every such case the write itself would ENOENT first.
#   - `-ef` has no timeout backstop, so a hung network mount can block the
#     check indefinitely — a fully hung D-state mount was never
#     interruptible either.
#   - Only `$HOME`/`${HOME}`/`$CLAUDE_CONFIG_DIR`/`${CLAUDE_CONFIG_DIR}`
#     are expanded in candidate text; every other shell-variable reference
#     stays under the shell-indirection gap above.
#   - The number of `_lib_shape_match`/`-ef` calls per Bash command is
#     unbounded — see `_lib.sh`'s own disclosure on `_lib_shape_match` for
#     why this is deliberate.
#
# Posture: raises the cost of a naive/cooperative write to claude-config.toml,
# not a hard boundary — every gap above traces to this being a fixed
# name-list text scan rather than a syscall trace, so an interpreter write,
# here-doc, `$(...)`-computed path, or unlisted write utility passes through
# untouched.
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
    # No brace-expansion check needed here: file_path is a literal string
    # handed straight to the filesystem, never parsed by a shell, so there
    # is no brace construct for a shell to expand.
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

# Flattened companion to COMMAND_UNQUOTED: a brace-split write-utility name
# or path token (`_config_s{et,et}`, `claude-config{.toml,-bak}`) reaches
# every raw-text scan below as one literal that matches nothing, even
# though bash itself brace-expands it before executing. Every detection
# check below that reads COMMAND_UNQUOTED is paired with an identical check
# against COMMAND_FLATTENED — see _lib.sh's _lib_brace_flatten for the
# flattening contract and test_hook_alignment.py's static sweep for the
# invariant that keeps a future scan from omitting its own pairing.
COMMAND_FLATTENED=$(_lib_brace_flatten "$COMMAND_UNQUOTED")
COMMAND_FLATTENED_EXIT=$?
if [ "$COMMAND_FLATTENED_EXIT" -eq 2 ]; then
  emit_deny "Config-file write — the command's brace-expansion nesting exceeds this scan's 16-pass flattening budget. Failing closed rather than allowing an unscanned Bash write that could reach the config state file — re-issue the command with each destination path written out literally instead."
  exit 0
fi
if [ "$COMMAND_FLATTENED_EXIT" -ne 0 ]; then
  emit_deny "could not flatten brace-expansion constructs in the command text (exit ${COMMAND_FLATTENED_EXIT}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach the config state file."
  exit 0
fi

# _config_write_scan_command COMMAND_TEXT
# Runs the redirect/utility-write extraction-and-shape-test pipeline once
# against COMMAND_TEXT, denying (exiting the hook) on a brace-bypass
# status, a fragment-split failure, or a candidate matching
# claude-config.toml. Called twice below — once against COMMAND_UNQUOTED,
# once against COMMAND_FLATTENED — so a brace-split write target is caught
# by the flattened pass even when the raw pass finds nothing; the two
# calls' outcomes are OR'd simply by both being able to deny and exit.
_config_write_scan_command() {
  local command_text="$1"
  local candidates candidates_exit
  candidates=$(_lib_redirect_candidates "$command_text")
  candidates_exit=$?
  if [ "$candidates_exit" -eq "$_LIB_BRACE_EXPANSION_BYPASS_STATUS" ]; then
    emit_deny "Config-file write — the command contains a brace-expansion construct (\`{a,b}\`/\`{a..b}\`), which bash expands before executing but this scan's own word-level candidate extraction cannot re-expand. Re-issue the command with each destination path written out literally instead."
    exit 0
  fi
  if [ "$candidates_exit" -ne 0 ]; then
    emit_deny "could not split the command into fragments (exit ${candidates_exit}) — sed may be missing, killed, or errored. Failing closed rather than allowing an unscanned Bash write that could reach the config state file."
    exit 0
  fi
  local candidate shape_status candidate_truncated
  local candidate_expanded resolved_root is_root_dir_match
  while IFS= read -r candidate; do
    [ -n "$candidate" ] || continue
    _lib_shape_match "$candidate" 'claude-config.toml'
    shape_status=$?
    if [ "$shape_status" -eq 2 ]; then
      candidate_truncated=$(printf '%s' "$candidate" | cut -c1-80)
      emit_deny "Config-file write — could not resolve the Claude Code config directory (CLAUDE_CONFIG_DIR is set to a relative path, or \$HOME is unset/empty) to verify '$candidate_truncated' is not the claude-config.toml state file."
      exit 0
    fi
    if [ "$shape_status" -eq 0 ]; then
      # A destination that IS the config root directory itself (Pass 2b in
      # _lib.sh's _lib_shape_match) matches independent of the write's
      # source file name, unlike every other pass, which requires the
      # candidate's own text to name claude-config.toml. Re-derive that
      # same directory-root identity here, via the same
      # $HOME/$CLAUDE_CONFIG_DIR expansion Pass 2b compares against. Honor
      # a directory-root match only when COMMAND_TEXT also names
      # claude-config.toml, its likely source argument — otherwise the
      # write is of an unrelated file and stays allowed.
      candidate_expanded="${candidate/#\~/$HOME}"
      candidate_expanded="${candidate_expanded//\$HOME/$HOME}"
      candidate_expanded="${candidate_expanded//\$\{HOME\}/$HOME}"
      candidate_expanded="${candidate_expanded//\$CLAUDE_CONFIG_DIR/${CLAUDE_CONFIG_DIR:-}}"
      candidate_expanded="${candidate_expanded//\$\{CLAUDE_CONFIG_DIR\}/${CLAUDE_CONFIG_DIR:-}}"
      resolved_root=$(_lib_config_dir 2>/dev/null)
      is_root_dir_match=1
      if [ -n "$resolved_root" ] \
        && [ "$candidate_expanded" -ef "$resolved_root" ] 2>/dev/null; then
        is_root_dir_match=0
      fi
      if [ "$is_root_dir_match" -eq 0 ] \
        && ! printf '%s' "$command_text" | grep -qFi 'claude-config.toml'; then
        continue
      fi
      candidate_truncated=$(printf '%s' "$candidate" | cut -c1-80)
      if [ "$is_root_dir_match" -eq 0 ]; then
        emit_deny "Config-file write — '$candidate_truncated' is (or resolves to) this machine's Claude Code config root directory, and the command also names claude-config.toml. A cp/mv/install/rsync/scp write into this directory lands on whatever the source's own basename is, so this could overwrite the config-key state file. Agent-mediated writes into the config root that also name claude-config.toml are denied; a human editing the state file directly, outside Claude Code, is unaffected. Use install.sh's interactive opt-in prompt or migrate-legacy-config.sh instead."
      else
        emit_deny "Config-file write — '$candidate_truncated' is (or resolves to) this machine's claude-config.toml. Agent-mediated writes to the consolidated config-key state file are denied; a human editing it directly, outside Claude Code, is unaffected. If you only meant to read the file, use cat/grep/less instead — this gate scans for write utilities, not reads."
      fi
      exit 0
    fi
  # Here-string over the already-captured candidates, not a nested command
  # substitution — matches _bash_marker_redirect_candidates's own
  # inner-loop precedent: the split's exit status is checked above, before
  # this loop starts.
  done <<< "$candidates"
}

_config_write_scan_command "$COMMAND_UNQUOTED"
_config_write_scan_command "$COMMAND_FLATTENED"

# Name-scan: denies any command mentioning `_config_set` as a literal
# substring, quote-blind (tested against the already quote-stripped command
# text above). Deliberately a bare substring test, not a positional-argument-
# grammar parser like marker.sh's — there is no legitimate Claude-Code-Bash-
# tool-call shape that types `_config_set` directly (the two sanctioned
# callers, install.sh's interactive prompt and migrate-legacy-config.sh's
# import phase, invoke it from inside a sourced/executed script, never as a
# literal top-level Bash command token), so no argument-shape allowlist is
# needed here the way marker.sh's chain/op/target shapes are.
if printf '%s' "$COMMAND_UNQUOTED" | grep -qF '_config_set' \
  || printf '%s' "$COMMAND_FLATTENED" | grep -qF '_config_set'; then
  emit_deny "Config-file write — command invokes _config_set, the sole sanctioned writer for claude-config.toml. Agent-mediated calls to it are denied; use install.sh's interactive opt-in prompt or migrate-legacy-config.sh instead."
fi

exit 0
