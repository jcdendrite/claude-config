#!/bin/bash
# hook-class: informational
# Gate: ask before editing .claude/settings*.json.
#
# Case-folding applies before all three shapes. Matches:
# - a literal `.claude/settings*.json` substring
# - that same shape after `_lib_realpath_m` alias normalization,
#   closing gap (h) (see
#   docs/design-decisions/global-claude-md-agent-core-and-main-session-groups.md's
#   Known gaps list). A `_lib_realpath_m` failure -- including hitting
#   `_LIB_REALPATH_M_FALLBACK_MAX_DEPTH` on a fallback-only host -- falls
#   back to the raw-path match alone, so a `.`/`//`-only alias (no `..`)
#   past that depth defeats both checks; see the same doc's Open residuals
#   section.
# - a settings file at the resolved config-dir root with no `.claude/`
#   segment, closing gap (c) (same doc)
#
# Why: settings.json edits that touch permissions.allow are security-sensitive
# and deserve a /review-permissions pass. A precise "does this edit touch
# permissions.allow" heuristic is fuzzy — the hook only sees new content, not
# a diff, and edits can move keys around without the word "allow" appearing —
# so we use a broad match and ask the user in the moment. Settings.json edits
# are rare enough that the "ask" mode is tolerable.

INPUT=$(cat)

if ! . "${0%/*}/_lib.sh" 2>/dev/null; then
  echo "[ask-review-permissions] could not source _lib.sh (run ./install.sh to pick up hook files this update added); settings.json edits will silently proceed without the ask decision" >&2
  exit 0
fi

# Unlike require-worktree-for-file-writes.sh's _lib_parse_tool_input_or_deny, an empty $TOOL here (e.g. a jq failure) falls through to allow, not deny.
TOOL=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_name // empty' 2>/dev/null)

case "$TOOL" in
  Edit|Write|MultiEdit) ;;
  *) exit 0 ;;
esac

FILE_PATH=$(printf '%s\n' "$INPUT" | _lib_jq -r '.tool_input.file_path // empty' 2>/dev/null)
[ -n "$FILE_PATH" ] || exit 0

FOLDED_RAW_PATH=$(printf '%s' "$FILE_PATH" | tr '[:upper:]' '[:lower:]')

MATCHED=0
# Match .claude/settings*.json paths: settings.json, settings.local.json, etc.
if printf '%s\n' "$FOLDED_RAW_PATH" | grep -qE '\.claude/settings[^/]*\.json$'; then
  MATCHED=1
fi

# Cheap prefilter: skip realpath unless the path already looks like a settings file, to avoid a subprocess spawn on every edit.
if [ "$MATCHED" -eq 0 ]; then
  case "$FOLDED_RAW_PATH" in
    *settings*.json)
      # Normalize `.`/`..`/duplicate slashes before matching:
      # - normalizes so an aliased path still asks (gap (h))
      # - compared alongside the raw path, not instead of it, so a symlinked
      #   `.claude` ancestor can't be resolved away to defeat the match
      # - falls back to the raw path on normalization failure
      FILE_NORMALIZE_OK=1
      NORMALIZED_PATH=$(_lib_realpath_m "$FILE_PATH" 2>/dev/null) || { NORMALIZED_PATH="$FILE_PATH"; FILE_NORMALIZE_OK=0; }
      FOLDED_NORMALIZED_PATH=$(printf '%s' "$NORMALIZED_PATH" | tr '[:upper:]' '[:lower:]')
      if printf '%s\n' "$FOLDED_NORMALIZED_PATH" | grep -qE '\.claude/settings[^/]*\.json$'; then
        MATCHED=1
      fi

      # gap (c): a config dir with no `.claude/` segment (e.g. a non-personal
      # CLAUDE_CONFIG_DIR account root) holds its settings file directly at its
      # root instead, so match that shape too.
      if [ "$MATCHED" -eq 0 ]; then
        CONFIG_DIR=$(_lib_config_dir 2>/dev/null) || CONFIG_DIR=""
        if [ -n "$CONFIG_DIR" ]; then
          CONFIG_NORMALIZE_OK=1
          NORMALIZED_CONFIG_DIR=$(_lib_realpath_m "$CONFIG_DIR" 2>/dev/null) || { NORMALIZED_CONFIG_DIR="$CONFIG_DIR"; CONFIG_NORMALIZE_OK=0; }
          # Compare two normalized forms only when both sides normalized -- a partial
          # failure would otherwise compare a normalized path against a raw config dir
          # (or vice versa), which can mismatch even when they name the same directory.
          if [ "$FILE_NORMALIZE_OK" -eq 1 ] && [ "$CONFIG_NORMALIZE_OK" -eq 1 ]; then
            COMPARE_PATH="$FOLDED_NORMALIZED_PATH"
            COMPARE_CONFIG_DIR="$NORMALIZED_CONFIG_DIR"
          else
            COMPARE_PATH="$FOLDED_RAW_PATH"
            COMPARE_CONFIG_DIR="$CONFIG_DIR"
          fi
          # _LIB_CONFIG_DIR_ESCAPE_SED_EXPR (_lib.sh) documents the escape
          # class's own coverage and known limitations.
          FOLDED_CONFIG_DIR=$(printf '%s' "$COMPARE_CONFIG_DIR" | tr '[:upper:]' '[:lower:]' | sed "$_LIB_CONFIG_DIR_ESCAPE_SED_EXPR")
          if printf '%s\n' "$COMPARE_PATH" | grep -qE "^${FOLDED_CONFIG_DIR}/settings[^/]*\.json\$"; then
            MATCHED=1
          fi
        fi
      fi
      ;;
    *) exit 0 ;;
  esac
fi

[ "$MATCHED" -eq 1 ] || exit 0

echo '{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"ask","permissionDecisionReason":"Edit to .claude/settings.json. If this changes permissions.allow, adds a bare tool name to permissions.deny, or changes permissions.defaultMode, run the /review-permissions skill first. Approve if unrelated (model, hooks, statusLine, enabledPlugins, etc)."}}'
